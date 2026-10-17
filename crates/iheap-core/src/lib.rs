//! An indexed priority queue that stores only priorities.
//!
//! [`IndexedHeap`] hands out a [`Handle`] for every pushed priority. The handle
//! stays valid until the entry is popped or removed, and allows `O(log n)`
//! priority updates and removals of arbitrary entries. Callers that need to
//! associate data with an entry keep it in their own handle-indexed storage;
//! handles are dense (`0..high_water_mark`) and recycled, so a plain `Vec` works.
//!
//! Internally this is a 4-ary min-heap of `(priority, handle)` nodes plus a
//! `handle -> heap position` map. A max-heap is obtained by negating priorities
//! on the way in and out, which keeps the hot comparison loop branch-free.

use std::fmt;
use std::mem;

/// Identifier of an entry in an [`IndexedHeap`].
pub type Handle = u32;

const ARITY: usize = 4;
/// Marks a handle slot that is currently not in use.
const VACANT: u32 = u32::MAX;
/// Largest number of simultaneously live entries (`VACANT` is reserved).
pub const MAX_LEN: usize = VACANT as usize;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    /// The handle does not refer to a live entry.
    InvalidHandle(Handle),
    /// Priorities must be comparable; NaN is rejected.
    NanPriority,
    /// More than [`MAX_LEN`] live entries were requested.
    CapacityOverflow,
    /// Batch inputs of different lengths.
    LengthMismatch { handles: usize, priorities: usize },
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::InvalidHandle(h) => write!(f, "invalid handle {h}"),
            Error::NanPriority => f.write_str("priority must not be NaN"),
            Error::CapacityOverflow => write!(f, "heap cannot hold more than {MAX_LEN} entries"),
            Error::LengthMismatch {
                handles,
                priorities,
            } => write!(f, "got {handles} handles but {priorities} priorities"),
        }
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;

/// Heap order of an [`IndexedHeap`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Order {
    /// Smallest priority on top.
    #[default]
    Min,
    /// Largest priority on top.
    Max,
}

impl Order {
    #[inline]
    fn sign(self) -> f64 {
        match self {
            Order::Min => 1.0,
            Order::Max => -1.0,
        }
    }
}

#[derive(Debug, Clone, Copy)]
struct Node {
    /// Priority multiplied by the heap's sign, so the heap is always a min-heap.
    key: f64,
    handle: Handle,
}

#[derive(Debug, Clone, Default)]
pub struct IndexedHeap {
    nodes: Vec<Node>,
    /// `positions[handle]` is the node index of `handle`, or `VACANT`.
    positions: Vec<u32>,
    /// Recycled handles, reused LIFO.
    free: Vec<Handle>,
    order: Order,
}

impl IndexedHeap {
    pub fn new(order: Order) -> Self {
        Self::with_capacity(order, 0)
    }

    pub fn with_capacity(order: Order, capacity: usize) -> Self {
        Self {
            nodes: Vec::with_capacity(capacity),
            positions: Vec::with_capacity(capacity),
            free: Vec::new(),
            order,
        }
    }

    #[inline]
    pub fn order(&self) -> Order {
        self.order
    }

    #[inline]
    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    #[inline]
    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    /// One past the largest handle ever issued since the last [`clear`](Self::clear).
    /// Every live handle is below this bound.
    #[inline]
    pub fn handle_bound(&self) -> usize {
        self.positions.len()
    }

    #[inline]
    pub fn contains(&self, handle: Handle) -> bool {
        self.position(handle).is_some()
    }

    pub fn priority(&self, handle: Handle) -> Option<f64> {
        self.position(handle)
            .map(|i| self.decode(self.nodes[i].key))
    }

    pub fn peek(&self) -> Option<(Handle, f64)> {
        self.nodes.first().map(|n| (n.handle, self.decode(n.key)))
    }

    /// Inserts `priority` and returns the handle of the new entry.
    pub fn push(&mut self, priority: f64) -> Result<Handle> {
        let key = self.encode(priority)?;
        if self.nodes.len() >= MAX_LEN {
            return Err(Error::CapacityOverflow);
        }
        let index = self.nodes.len();
        let handle = self.allocate(index);
        self.nodes.push(Node { key, handle });
        self.sift_up(index);
        Ok(handle)
    }

    /// Inserts all `priorities`, returning their handles in input order.
    ///
    /// The operation is atomic: on error nothing is inserted. Large batches are
    /// appended and re-heapified in `O(n)` instead of `O(k log n)`.
    pub fn push_many(&mut self, priorities: &[f64]) -> Result<Vec<Handle>> {
        let keys = priorities
            .iter()
            .map(|&p| self.encode(p))
            .collect::<Result<Vec<_>>>()?;
        if MAX_LEN - self.nodes.len() < keys.len() {
            return Err(Error::CapacityOverflow);
        }
        let start = self.nodes.len();
        let rebuild = prefer_rebuild(keys.len(), start + keys.len());
        self.nodes.reserve(keys.len());
        let mut handles = Vec::with_capacity(keys.len());
        for key in keys {
            let index = self.nodes.len();
            let handle = self.allocate(index);
            self.nodes.push(Node { key, handle });
            if !rebuild {
                self.sift_up(index);
            }
            handles.push(handle);
        }
        if rebuild {
            self.heapify();
        }
        Ok(handles)
    }

    /// Removes and returns the top entry.
    pub fn pop(&mut self) -> Option<(Handle, f64)> {
        let last = self.nodes.pop()?;
        let top = match self.nodes.first_mut() {
            Some(root) => {
                let top = mem::replace(root, last);
                self.sift_down(0);
                top
            }
            None => last,
        };
        self.release(top.handle);
        Some((top.handle, self.decode(top.key)))
    }

    /// Push followed by pop, fused into a single sift.
    ///
    /// If the new entry would be popped straight away (the heap is empty or
    /// the top does not strictly precede `priority`), the heap is unchanged
    /// and `None` is returned. Otherwise the top entry is popped and its
    /// handle is reassigned to the new entry; returns that handle together
    /// with the popped priority.
    pub fn push_pop(&mut self, priority: f64) -> Result<Option<(Handle, f64)>> {
        let key = self.encode(priority)?;
        match self.nodes.first_mut() {
            Some(root) if root.key < key => {
                let old = mem::replace(&mut root.key, key);
                let handle = root.handle;
                self.sift_down(0);
                Ok(Some((handle, self.decode(old))))
            }
            _ => Ok(None),
        }
    }

    /// Pops up to `n` entries in priority order.
    pub fn pop_many(&mut self, n: usize) -> Vec<(Handle, f64)> {
        let n = n.min(self.len());
        (0..n).filter_map(|_| self.pop()).collect()
    }

    /// Sets the priority of `handle` and returns the previous one.
    pub fn update(&mut self, handle: Handle, priority: f64) -> Result<f64> {
        let index = self.position(handle).ok_or(Error::InvalidHandle(handle))?;
        let key = self.encode(priority)?;
        let old = mem::replace(&mut self.nodes[index].key, key);
        if key < old {
            self.sift_up(index);
        } else if key > old {
            self.sift_down(index);
        }
        Ok(self.decode(old))
    }

    /// Sets many priorities at once. Atomic: on error nothing is changed.
    /// If a handle appears more than once, the last priority wins.
    pub fn update_many(&mut self, handles: &[Handle], priorities: &[f64]) -> Result<()> {
        if handles.len() != priorities.len() {
            return Err(Error::LengthMismatch {
                handles: handles.len(),
                priorities: priorities.len(),
            });
        }
        let mut targets = Vec::with_capacity(handles.len());
        for (&h, &p) in handles.iter().zip(priorities) {
            let index = self.position(h).ok_or(Error::InvalidHandle(h))?;
            targets.push((index, self.encode(p)?));
        }
        if prefer_rebuild(targets.len(), self.len()) {
            for (index, key) in targets {
                self.nodes[index].key = key;
            }
            self.heapify();
        } else {
            for (&h, &p) in handles.iter().zip(priorities) {
                self.update(h, p)?;
            }
        }
        Ok(())
    }

    /// Removes `handle` and returns its priority.
    pub fn remove(&mut self, handle: Handle) -> Result<f64> {
        let index = self.position(handle).ok_or(Error::InvalidHandle(handle))?;
        let removed = self.nodes[index];
        let last = self.nodes.pop().expect("heap holds the removed node");
        if index < self.nodes.len() {
            self.nodes[index] = last;
            if index > 0 && last.key < self.nodes[(index - 1) / ARITY].key {
                self.sift_up(index);
            } else {
                self.sift_down(index);
            }
        }
        self.release(handle);
        Ok(self.decode(removed.key))
    }

    /// Removes every entry and resets handle allocation.
    pub fn clear(&mut self) {
        self.nodes.clear();
        self.positions.clear();
        self.free.clear();
    }

    /// Iterates over live entries in heap (not sorted) order.
    pub fn iter(&self) -> impl ExactSizeIterator<Item = (Handle, f64)> + '_ {
        self.nodes.iter().map(|n| (n.handle, self.decode(n.key)))
    }

    #[inline]
    fn position(&self, handle: Handle) -> Option<usize> {
        match self.positions.get(handle as usize) {
            Some(&p) if p != VACANT => Some(p as usize),
            _ => None,
        }
    }

    #[inline]
    fn encode(&self, priority: f64) -> Result<f64> {
        if priority.is_nan() {
            return Err(Error::NanPriority);
        }
        Ok(priority * self.order.sign())
    }

    #[inline]
    fn decode(&self, key: f64) -> f64 {
        key * self.order.sign()
    }

    /// Caller guarantees fewer than `MAX_LEN` live entries, so a fresh handle fits.
    fn allocate(&mut self, index: usize) -> Handle {
        match self.free.pop() {
            Some(handle) => {
                self.positions[handle as usize] = index as u32;
                handle
            }
            None => {
                self.positions.push(index as u32);
                (self.positions.len() - 1) as Handle
            }
        }
    }

    #[inline]
    fn release(&mut self, handle: Handle) {
        self.positions[handle as usize] = VACANT;
        self.free.push(handle);
    }

    #[inline]
    fn place(&mut self, index: usize, node: Node) {
        self.nodes[index] = node;
        self.positions[node.handle as usize] = index as u32;
    }

    /// Moves the node at `index` towards the root, shifting parents down into
    /// the hole instead of swapping.
    fn sift_up(&mut self, mut index: usize) {
        let node = self.nodes[index];
        while index > 0 {
            let parent = (index - 1) / ARITY;
            let p = self.nodes[parent];
            if node.key >= p.key {
                break;
            }
            self.place(index, p);
            index = parent;
        }
        self.place(index, node);
    }

    fn sift_down(&mut self, mut index: usize) {
        let len = self.nodes.len();
        let node = self.nodes[index];
        while let Some(best) = self.min_child(index, len) {
            if self.nodes[best].key >= node.key {
                break;
            }
            self.place(index, self.nodes[best]);
            index = best;
        }
        self.place(index, node);
    }

    /// Index of the smallest child of `index`, if it has any.
    #[inline(always)]
    fn min_child(&self, index: usize, len: usize) -> Option<usize> {
        let first = index * ARITY + 1;
        if first >= len {
            return None;
        }
        let mut best = first;
        let mut best_key = self.nodes[first].key;
        for child in first + 1..(first + ARITY).min(len) {
            let key = self.nodes[child].key;
            if key < best_key {
                best = child;
                best_key = key;
            }
        }
        Some(best)
    }

    /// Restores the heap property over all nodes in `O(n)`.
    /// Positions are refreshed for every node, including leaves.
    fn heapify(&mut self) {
        for (index, node) in self.nodes.iter().enumerate() {
            self.positions[node.handle as usize] = index as u32;
        }
        if self.nodes.len() > 1 {
            for index in (0..=(self.nodes.len() - 2) / ARITY).rev() {
                self.sift_down(index);
            }
        }
    }

    #[cfg(test)]
    fn assert_invariants(&self) {
        for (i, n) in self.nodes.iter().enumerate() {
            assert_eq!(self.positions[n.handle as usize] as usize, i);
            if i > 0 {
                assert!(
                    self.nodes[(i - 1) / ARITY].key <= n.key,
                    "heap order at {i}"
                );
            }
        }
        let live = self.positions.iter().filter(|&&p| p != VACANT).count();
        assert_eq!(live, self.nodes.len());
        assert_eq!(live + self.free.len(), self.positions.len());
    }
}

/// Whether touching `k` of `n` nodes is cheaper via an `O(n)` heapify than via
/// `k` individual `O(log n)` sifts.
#[inline]
fn prefer_rebuild(k: usize, n: usize) -> bool {
    n > 1 && k.saturating_mul(n.ilog2() as usize) >= n
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    /// Small deterministic PRNG so tests need no dependencies.
    struct XorShift(u64);

    impl XorShift {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            self.0
        }
        fn below(&mut self, n: u64) -> u64 {
            self.next() % n
        }
        fn priority(&mut self) -> f64 {
            // Narrow range to exercise ties.
            self.below(50) as f64 - 25.0
        }
    }

    fn drain(heap: &mut IndexedHeap) -> Vec<f64> {
        std::iter::from_fn(|| heap.pop().map(|(_, p)| p)).collect()
    }

    #[test]
    fn pops_in_order() {
        let mut heap = IndexedHeap::new(Order::Min);
        for p in [5.0, 1.0, 4.0, 1.5, -3.0, 9.0] {
            heap.push(p).unwrap();
        }
        assert_eq!(drain(&mut heap), vec![-3.0, 1.0, 1.5, 4.0, 5.0, 9.0]);
        assert!(heap.pop().is_none());
    }

    #[test]
    fn max_order() {
        let mut heap = IndexedHeap::new(Order::Max);
        heap.push_many(&[3.0, 7.0, -1.0, f64::INFINITY]).unwrap();
        assert_eq!(drain(&mut heap), vec![f64::INFINITY, 7.0, 3.0, -1.0]);
    }

    #[test]
    fn rejects_nan_atomically() {
        let mut heap = IndexedHeap::new(Order::Min);
        let h = heap.push(1.0).unwrap();
        assert_eq!(heap.push(f64::NAN), Err(Error::NanPriority));
        assert_eq!(heap.push_many(&[2.0, f64::NAN]), Err(Error::NanPriority));
        assert_eq!(heap.update(h, f64::NAN), Err(Error::NanPriority));
        assert_eq!(
            heap.update_many(&[h, h], &[0.0, f64::NAN]),
            Err(Error::NanPriority)
        );
        assert_eq!(heap.len(), 1);
        assert_eq!(heap.priority(h), Some(1.0));
    }

    #[test]
    fn handles_are_recycled_and_invalidated() {
        let mut heap = IndexedHeap::new(Order::Min);
        let a = heap.push(1.0).unwrap();
        let b = heap.push(2.0).unwrap();
        assert_eq!(heap.remove(a), Ok(1.0));
        assert_eq!(heap.remove(a), Err(Error::InvalidHandle(a)));
        assert_eq!(heap.update(a, 0.0), Err(Error::InvalidHandle(a)));
        assert_eq!(heap.push(3.0), Ok(a));
        assert_eq!(heap.handle_bound(), 2);
        assert_eq!(heap.peek(), Some((b, 2.0)));
        assert_eq!(
            heap.update_many(&[b, 99], &[0.0, 0.0]),
            Err(Error::InvalidHandle(99))
        );
        assert_eq!(heap.priority(b), Some(2.0));
    }

    #[test]
    fn push_pop_reuses_top_handle() {
        let mut heap = IndexedHeap::new(Order::Min);
        assert_eq!(heap.push_pop(1.0), Ok(None));
        let a = heap.push(1.0).unwrap();
        let b = heap.push(2.0).unwrap();
        assert_eq!(heap.push_pop(1.0), Ok(None), "ties keep the new entry out");
        assert_eq!(heap.push_pop(0.5), Ok(None));
        assert_eq!(heap.push_pop(3.0), Ok(Some((a, 1.0))));
        assert_eq!(heap.peek(), Some((b, 2.0)));
        assert_eq!(heap.priority(a), Some(3.0));
        assert_eq!(heap.push_pop(f64::NAN), Err(Error::NanPriority));
        heap.assert_invariants();
    }

    /// The Python layer relies on this to bulk-load without a handle lookup.
    #[test]
    fn fresh_push_many_issues_sequential_handles() {
        let mut heap = IndexedHeap::new(Order::Min);
        heap.push_many(&[1.0, 2.0, 3.0]).unwrap();
        heap.pop();
        heap.clear();
        assert_eq!(heap.push_many(&[3.0, 1.0, 2.0, 0.0]), Ok(vec![0, 1, 2, 3]));
    }

    #[test]
    fn clear_resets_handles() {
        let mut heap = IndexedHeap::new(Order::Min);
        heap.push_many(&[1.0, 2.0, 3.0]).unwrap();
        heap.clear();
        assert!(heap.is_empty());
        assert_eq!(heap.handle_bound(), 0);
        assert_eq!(heap.push(1.0), Ok(0));
    }

    /// Differential test against a `HashMap` model under a random operation mix.
    #[test]
    fn matches_model_under_random_operations() {
        for (seed, order) in [(1, Order::Min), (2, Order::Max), (3, Order::Min)] {
            let mut rng = XorShift(0x9E37_79B9_7F4A_7C15 ^ seed);
            let mut heap = IndexedHeap::new(order);
            let mut model: HashMap<Handle, f64> = HashMap::new();
            let better = |a: f64, b: f64| match order {
                Order::Min => a < b,
                Order::Max => a > b,
            };
            for _ in 0..4_000 {
                let live: Vec<Handle> = model.keys().copied().collect();
                let pick = |rng: &mut XorShift| live[rng.below(live.len() as u64) as usize];
                match rng.below(8) {
                    0 | 1 => {
                        let p = rng.priority();
                        let h = heap.push(p).unwrap();
                        assert!(model.insert(h, p).is_none());
                    }
                    2 => {
                        let ps: Vec<f64> = (0..rng.below(16)).map(|_| rng.priority()).collect();
                        let hs = heap.push_many(&ps).unwrap();
                        for (h, p) in hs.into_iter().zip(ps) {
                            assert!(model.insert(h, p).is_none());
                        }
                    }
                    3 => match heap.pop() {
                        Some((h, p)) => {
                            assert_eq!(model.remove(&h), Some(p));
                            assert!(model.values().all(|&q| !better(q, p)));
                        }
                        None => assert!(model.is_empty()),
                    },
                    4 if !live.is_empty() => {
                        let h = pick(&mut rng);
                        assert_eq!(heap.remove(h).unwrap(), model.remove(&h).unwrap());
                    }
                    5 | 6 if !live.is_empty() => {
                        let h = pick(&mut rng);
                        let p = rng.priority();
                        assert_eq!(heap.update(h, p).unwrap(), model.insert(h, p).unwrap());
                    }
                    7 if !live.is_empty() => {
                        let k = rng.below(live.len() as u64 + 1) as usize;
                        let hs: Vec<Handle> = (0..k).map(|_| pick(&mut rng)).collect();
                        let ps: Vec<f64> = (0..k).map(|_| rng.priority()).collect();
                        heap.update_many(&hs, &ps).unwrap();
                        for (h, p) in hs.into_iter().zip(ps) {
                            model.insert(h, p);
                        }
                    }
                    _ => {}
                }
                heap.assert_invariants();
                assert_eq!(heap.len(), model.len());
                if let Some((h, p)) = heap.peek() {
                    assert_eq!(model[&h], p);
                }
            }
        }
    }
}
