//! An indexed priority queue that stores only priorities.
//!
//! [`IndexedHeap`] hands out a [`Handle`] for every pushed priority. The handle
//! stays valid until the entry is popped or removed, and allows `O(log n)`
//! priority updates and removals of arbitrary entries. Callers that need to
//! associate data with an entry keep it in their own handle-indexed storage;
//! handles are dense (`0..handle_bound`) and recycled, so a plain `Vec` works.
//!
//! # Design
//!
//! The queue is an **8-ary tournament (winner) tree**, not a binary heap:
//!
//! * **Leaf `h` holds the priority of handle `h`**, so there is no
//!   handle-to-position map. Leaves are grouped by 8 into one 64-byte cache
//!   line; every internal node stores the winner (smallest key and its leaf)
//!   of a group of 8 children, and 8 nodes form a 128-byte group.
//! * **Every operation writes one leaf and replays the path to the root.** On
//!   each level, the node above leaf `h` is `h >> 3(level + 1)`: the path is
//!   known before any key is read. In a d-ary heap's sift-down, the next
//!   address depends on the current comparison; here the winner among the
//!   *other* siblings on each level is independent of the value being
//!   replayed, so the CPU computes all levels' reductions in parallel and the
//!   serial chain per level is a single compare-and-select.
//! * Updates stop as soon as a node's winner comes out unchanged, so insert,
//!   decrease-key and changes to non-winning entries usually finish after one
//!   or two comparisons. Only `pop` (and raising the current winner) re-reads
//!   sibling groups, with NEON on aarch64.
//! * Growth only appends: a node's index does not depend on the tree's size.
//!   Bulk loads write the leaves and rebuild bottom-up in `O(n)`.
//! * Priorities are stored as order-preserving `u64` keys: integer compares, a
//!   total order (`-0.0` sorts before `+0.0` and both round-trip), and a
//!   sentinel for vacant leaves that no real priority can collide with. A
//!   max-queue simply complements the keys.

mod group;
mod heap;
mod key;

use std::fmt;

pub use heap::IndexedHeap;

/// Identifier of an entry in an [`IndexedHeap`].
pub type Handle = u32;

/// Largest number of handles a heap can issue.
pub const MAX_LEN: usize = Handle::MAX as usize;

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

/// Whether touching `k` of `n` entries is cheaper via an `O(n)` rebuild than
/// via `k` individual `O(log n)` updates.
#[inline]
pub(crate) fn prefer_rebuild(k: usize, n: usize) -> bool {
    n > 1 && k.saturating_mul(n.ilog2() as usize) >= n
}
