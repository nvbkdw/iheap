//! The indexed priority queue: an 8-ary tournament (winner) tree.

use crate::group::{
    group_winner, leaf_group_winner, winner, LeafGroup, Node, NodeGroup, EMPTY, EMPTY_NODES,
    FANOUT, NO_SKIP, VACANT_LEAVES,
};
use crate::key::{decode, encode, VACANT};
use crate::{prefer_rebuild, Error, Handle, Order, Result, MAX_LEN};

/// Indexed priority queue of `f64` priorities addressed by [`Handle`]s.
///
/// See the [crate documentation](crate) for the design.
#[derive(Debug, Clone)]
pub struct IndexedHeap {
    /// `leaves[h / 8].0[h % 8]`: key of handle `h`, or `VACANT`.
    leaves: Vec<LeafGroup>,
    /// `levels[l]` holds node `i` of internal level `l` at `[i / 8].0[i % 8]`;
    /// node `i` is the winner of children group `i` on the level below (leaf
    /// group `i` for `l == 0`). The last level (or the leaves, if there are no
    /// levels) is a single group, whose winner is `root`.
    levels: Vec<Vec<NodeGroup>>,
    /// Overall winner. Kept here rather than as the top of `levels`: every
    /// `pop` starts from it, so it is one load instead of three.
    root: Node,
    /// One past the largest handle issued since the last `clear`.
    bound: usize,
    len: usize,
    /// Recycled handles, reused LIFO.
    free: Vec<Handle>,
    order: Order,
}

impl Default for IndexedHeap {
    fn default() -> Self {
        Self::new(Order::Min)
    }
}

impl IndexedHeap {
    pub fn new(order: Order) -> Self {
        Self::with_capacity(order, 0)
    }

    /// Pre-allocates room for `capacity` handles.
    pub fn with_capacity(order: Order, capacity: usize) -> Self {
        let mut heap = Self {
            leaves: Vec::new(),
            levels: Vec::new(),
            root: EMPTY,
            bound: 0,
            len: 0,
            free: Vec::new(),
            order,
        };
        heap.reserve_leaves(capacity);
        heap
    }

    #[inline]
    pub fn order(&self) -> Order {
        self.order
    }

    #[inline]
    pub fn len(&self) -> usize {
        self.len
    }

    #[inline]
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// One past the largest handle issued since the last [`clear`](Self::clear).
    /// Every live handle is below this bound.
    #[inline]
    pub fn handle_bound(&self) -> usize {
        self.bound
    }

    #[inline]
    pub fn contains(&self, handle: Handle) -> bool {
        self.live_key(handle).is_some()
    }

    pub fn priority(&self, handle: Handle) -> Option<f64> {
        self.live_key(handle).map(|k| decode(k, self.order))
    }

    pub fn peek(&self) -> Option<(Handle, f64)> {
        (self.len > 0).then(|| (self.root.handle(), decode(self.root.key, self.order)))
    }

    /// Inserts `priority` and returns the handle of the new entry.
    pub fn push(&mut self, priority: f64) -> Result<Handle> {
        let key = encode(priority, self.order)?;
        if self.len >= MAX_LEN {
            return Err(Error::CapacityOverflow);
        }
        let handle = self.allocate();
        self.set_leaf(handle, key);
        Ok(handle)
    }

    /// Inserts all `priorities`, returning their handles in input order. On a
    /// fresh or cleared heap these are `0, 1, ..., n - 1`.
    ///
    /// The operation is atomic: on error nothing is inserted. Large batches are
    /// written directly and the tree rebuilt in `O(n)`.
    pub fn push_many(&mut self, priorities: &[f64]) -> Result<Vec<Handle>> {
        let keys = priorities
            .iter()
            .map(|&p| encode(p, self.order))
            .collect::<Result<Vec<_>>>()?;
        if MAX_LEN - self.len < keys.len() {
            return Err(Error::CapacityOverflow);
        }
        let rebuild = prefer_rebuild(keys.len(), self.len + keys.len());
        self.reserve_leaves(self.bound + keys.len());
        let mut handles = Vec::with_capacity(keys.len());
        for key in keys {
            let handle = self.allocate();
            if rebuild {
                self.write_leaf(handle, key);
            } else {
                self.set_leaf(handle, key);
            }
            handles.push(handle);
        }
        if rebuild {
            self.rebuild();
        }
        Ok(handles)
    }

    /// Removes and returns the top entry.
    pub fn pop(&mut self) -> Option<(Handle, f64)> {
        if self.len == 0 {
            return None;
        }
        let top = self.root;
        self.replace_winner(VACANT);
        self.len -= 1;
        self.free.push(top.handle());
        Some((top.handle(), decode(top.key, self.order)))
    }

    /// Push followed by pop, fused into a single replay.
    ///
    /// If the new entry would be popped straight away (the heap is empty or
    /// the top does not strictly precede `priority`), the heap is unchanged
    /// and `None` is returned. Otherwise the top entry is popped and its
    /// handle is reassigned to the new entry; returns that handle together
    /// with the popped priority.
    pub fn push_pop(&mut self, priority: f64) -> Result<Option<(Handle, f64)>> {
        let key = encode(priority, self.order)?;
        let top = self.root;
        if self.len == 0 || top.key >= key {
            return Ok(None);
        }
        self.replace_winner(key);
        Ok(Some((top.handle(), decode(top.key, self.order))))
    }

    /// Pops up to `n` entries in priority order.
    pub fn pop_many(&mut self, n: usize) -> Vec<(Handle, f64)> {
        let n = n.min(self.len);
        (0..n).filter_map(|_| self.pop()).collect()
    }

    /// Sets the priority of `handle` and returns the previous one.
    pub fn update(&mut self, handle: Handle, priority: f64) -> Result<f64> {
        let old = self.live_key(handle).ok_or(Error::InvalidHandle(handle))?;
        let key = encode(priority, self.order)?;
        self.set_leaf(handle, key);
        Ok(decode(old, self.order))
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
            self.live_key(h).ok_or(Error::InvalidHandle(h))?;
            targets.push((h, encode(p, self.order)?));
        }
        if prefer_rebuild(targets.len(), self.len) {
            for (h, key) in targets {
                self.write_leaf(h, key);
            }
            self.rebuild();
        } else {
            for (h, key) in targets {
                self.set_leaf(h, key);
            }
        }
        Ok(())
    }

    /// Removes `handle` and returns its priority.
    pub fn remove(&mut self, handle: Handle) -> Result<f64> {
        let key = self.live_key(handle).ok_or(Error::InvalidHandle(handle))?;
        self.set_leaf(handle, VACANT);
        self.len -= 1;
        self.free.push(handle);
        Ok(decode(key, self.order))
    }

    /// Removes every entry and resets handle allocation. Keeps the memory.
    pub fn clear(&mut self) {
        self.leaves.fill(VACANT_LEAVES);
        for level in &mut self.levels {
            level.fill(EMPTY_NODES);
        }
        self.root = EMPTY;
        self.bound = 0;
        self.len = 0;
        self.free.clear();
    }

    /// Iterates over live entries in handle order.
    pub fn iter(&self) -> impl Iterator<Item = (Handle, f64)> + '_ {
        (0..self.bound as Handle)
            .filter_map(|h| self.live_key(h).map(|k| (h, decode(k, self.order))))
    }

    #[inline]
    fn live_key(&self, handle: Handle) -> Option<u64> {
        let h = handle as usize;
        if h >= self.bound {
            return None;
        }
        let key = self.leaves[h / FANOUT].0[h % FANOUT];
        (key != VACANT).then_some(key)
    }

    #[inline(always)]
    fn write_leaf(&mut self, handle: Handle, key: u64) {
        let h = handle as usize;
        self.leaves[h / FANOUT].0[h % FANOUT] = key;
    }

    /// Returns a vacant handle: a recycled one, else the next fresh one.
    /// Caller guarantees fewer than `MAX_LEN` live entries.
    #[inline]
    fn allocate(&mut self) -> Handle {
        self.len += 1;
        if let Some(handle) = self.free.pop() {
            return handle;
        }
        if self.bound == self.leaves.len() * FANOUT {
            self.reserve_leaves(self.bound + 1);
        }
        self.bound += 1;
        (self.bound - 1) as Handle
    }

    /// Makes room for `leaves` leaves. New nodes cover only vacant leaves, so
    /// `EMPTY` is already their correct winner; nothing is rebuilt. When the
    /// top level gains a second group, a new level is added above it whose
    /// first node is the old root, and the root itself is unchanged.
    #[cold]
    fn reserve_leaves(&mut self, leaves: usize) {
        let mut groups = leaves.div_ceil(FANOUT).max(1); // groups on the leaf level
        if self.leaves.len() < groups {
            self.leaves.resize(groups, VACANT_LEAVES);
        }
        let mut level = 0;
        while groups > 1 {
            let level_groups = groups.div_ceil(FANOUT); // for `groups` nodes
            if level == self.levels.len() {
                let mut top = vec![EMPTY_NODES; level_groups];
                top[0].0[0] = self.root;
                self.levels.push(top);
            } else if self.levels[level].len() < level_groups {
                self.levels[level].resize(level_groups, EMPTY_NODES);
            }
            groups = level_groups;
            level += 1;
        }
    }

    /// Gives the overall winner a new key and replays its whole path. It won
    /// every node on the path, so each node is recomputed from its other
    /// children, with no tests in between.
    ///
    /// The other children's winner on each level depends only on the path,
    /// not on the value being replayed, so the CPU computes all levels'
    /// reductions in parallel; the serial chain per level is one
    /// compare-and-select. Siblings are read *before* our slot in their group
    /// is written, so the loads never wait on store-to-load forwarding.
    #[inline(always)]
    fn replace_winner(&mut self, key: u64) {
        let handle = self.root.handle();
        let h = handle as usize;
        let (mut index, lane) = (h / FANOUT, h % FANOUT);
        let others = leaf_group_winner(&self.leaves[index], index, lane);
        self.leaves[index].0[lane] = key;
        let mut best = winner(Node::new(key, handle), others);
        for level in &mut self.levels {
            let (g, lane) = (index / FANOUT, index % FANOUT);
            let others = group_winner(&level[g], lane);
            level[g].0[lane] = best;
            best = winner(best, others);
            index = g;
        }
        self.root = best;
    }

    /// Sets leaf `handle` to `key` and restores the winners on its path.
    ///
    /// Phase A covers the nodes the leaf used to win: each is recomputed from
    /// its other children. Above them a sibling had won; siblings are
    /// unchanged, so one comparison decides (phase B), and once a node comes
    /// out unchanged no ancestor can change. The root is the node on level
    /// `levels.len()`. Insert, decrease-key and changes to non-winning leaves
    /// thus mostly finish after a comparison or two.
    #[inline(always)]
    fn set_leaf(&mut self, handle: Handle, key: u64) {
        let h = handle as usize;
        let (mut index, leaf_lane) = (h / FANOUT, h % FANOUT);
        let old = Node::new(self.leaves[index].0[leaf_lane], handle);
        let mut new = Node::new(key, handle);
        let top = self.levels.len();
        let mut level = 0;

        // As in `replace_winner`, siblings are read before their group is
        // written.
        let phase_a = self.stored(0, index) == old;
        let mut others = EMPTY;
        if phase_a {
            others = leaf_group_winner(&self.leaves[index], index, leaf_lane);
        }
        self.leaves[index].0[leaf_lane] = key;
        if phase_a {
            loop {
                new = winner(new, others);
                if new == old {
                    return;
                }
                if level == top {
                    self.root = new;
                    return;
                }
                let (g, lane) = (index / FANOUT, index % FANOUT);
                let parent_was_ours = self.stored(level + 1, g) == old;
                if parent_was_ours {
                    others = group_winner(&self.levels[level][g], lane);
                }
                self.levels[level][g].0[lane] = new;
                level += 1;
                index = g;
                if !parent_was_ours {
                    break;
                }
            }
        }

        // Phase B.
        while level < top {
            let (g, lane) = (index / FANOUT, index % FANOUT);
            let stored = self.levels[level][g].0[lane];
            let value = winner(stored, new);
            if value == stored {
                return;
            }
            self.levels[level][g].0[lane] = value;
            new = value;
            index = g;
            level += 1;
        }
        self.root = winner(self.root, new);
    }

    /// Stored winner of node `index` on `level` (the root on the top level).
    #[inline(always)]
    fn stored(&self, level: usize, index: usize) -> Node {
        match self.levels.get(level) {
            Some(nodes) => nodes[index / FANOUT].0[index % FANOUT],
            None => self.root,
        }
    }

    /// Recomputes every internal node and the root from the leaves in `O(n)`.
    fn rebuild(&mut self) {
        let mut nodes = self.leaves.len(); // nodes on the current level
        for level in 0..self.levels.len() {
            for i in 0..nodes {
                let w = match level {
                    0 => leaf_group_winner(&self.leaves[i], i, NO_SKIP),
                    _ => group_winner(&self.levels[level - 1][i], NO_SKIP),
                };
                self.levels[level][i / FANOUT].0[i % FANOUT] = w;
            }
            nodes = nodes.div_ceil(FANOUT);
        }
        self.root = match self.levels.last() {
            Some(top) => group_winner(&top[0], NO_SKIP),
            None => leaf_group_winner(&self.leaves[0], 0, NO_SKIP),
        };
    }

    /// Checks every node against its children (test support).
    #[cfg(test)]
    pub(crate) fn assert_invariants(&self) {
        let mut below: Vec<Node> = (0..self.leaves.len() * FANOUT)
            .map(|h| Node::new(self.leaves[h / FANOUT].0[h % FANOUT], h as Handle))
            .collect();
        for nodes in &self.levels {
            let level: Vec<Node> = nodes.iter().flat_map(|g| g.0).collect();
            for (i, chunk) in below.chunks(FANOUT).enumerate() {
                let want = chunk.iter().map(|n| n.key).min().unwrap();
                assert_eq!(level[i].key, want, "node {i}");
                assert!(
                    chunk.contains(&level[i]) || want == VACANT,
                    "winner of node {i}"
                );
            }
            below = level;
        }
        let want = below[..FANOUT].iter().map(|n| n.key).min().unwrap();
        assert_eq!(self.root.key, want, "root");
        let live = (0..self.bound)
            .filter(|&h| self.live_key(h as Handle).is_some())
            .count();
        assert_eq!(live, self.len);
        assert_eq!(live + self.free.len(), self.bound);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct XorShift(u64);

    impl XorShift {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            self.0
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
    fn infinite_priorities_are_not_vacant() {
        let mut heap = IndexedHeap::new(Order::Min);
        let a = heap.push(f64::INFINITY).unwrap();
        assert_eq!(heap.peek(), Some((a, f64::INFINITY)));
        let mut heap = IndexedHeap::new(Order::Max);
        let b = heap.push(f64::NEG_INFINITY).unwrap();
        assert_eq!(heap.pop(), Some((b, f64::NEG_INFINITY)));
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
        heap.assert_invariants();
    }

    #[test]
    fn handles_are_recycled_and_invalidated() {
        let mut heap = IndexedHeap::new(Order::Min);
        let a = heap.push(1.0).unwrap();
        let b = heap.push(2.0).unwrap();
        assert_eq!(heap.remove(a), Ok(1.0));
        assert_eq!(heap.remove(a), Err(Error::InvalidHandle(a)));
        assert_eq!(heap.update(a, 0.0), Err(Error::InvalidHandle(a)));
        assert!(!heap.contains(a) && !heap.contains(1_000));
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
        assert!(heap.is_empty() && heap.peek().is_none());
        assert_eq!(heap.handle_bound(), 0);
        assert_eq!(heap.push(1.0), Ok(0));
        heap.assert_invariants();
    }

    /// Sizes straddling every level boundary, built both incrementally (tree
    /// growth) and in bulk (rebuild), must sort and keep the tree consistent.
    #[test]
    fn growth_and_rebuild_across_level_boundaries() {
        let mut rng = XorShift(42);
        for n in [1, 7, 8, 9, 63, 64, 65, 511, 512, 513, 4096, 4097, 40_000] {
            let ps: Vec<f64> = (0..n).map(|_| (rng.next() >> 11) as f64 - 1e15).collect();
            for bulk in [false, true] {
                let mut heap = IndexedHeap::new(Order::Min);
                if bulk {
                    heap.push_many(&ps).unwrap();
                } else {
                    for &p in &ps {
                        heap.push(p).unwrap();
                    }
                }
                heap.assert_invariants();
                let mut sorted = ps.clone();
                sorted.sort_by(f64::total_cmp);
                assert_eq!(drain(&mut heap), sorted, "n = {n}, bulk = {bulk}");
                heap.assert_invariants();
            }
        }
    }

    #[test]
    fn preserves_signed_zero() {
        let mut heap = IndexedHeap::new(Order::Min);
        let pos = heap.push(0.0).unwrap();
        let neg = heap.push(-0.0).unwrap();
        assert!(heap.priority(pos).unwrap().is_sign_positive());
        assert!(heap.priority(neg).unwrap().is_sign_negative());
        // Encoded keys are totally ordered: -0.0 precedes +0.0.
        assert_eq!(heap.pop().map(|(h, _)| h), Some(neg));
    }
}
