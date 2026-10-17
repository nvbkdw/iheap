//! Tree nodes, cache-line groups of 8, and the 8-way winner reductions.
//!
//! A group of 8 leaf keys is one 64-byte line; a group of 8 internal nodes is
//! 128 bytes (one line on Apple silicon, two adjacent lines on x86). The
//! reductions are on the critical path of every `pop`, so on little-endian
//! aarch64 with NEON (every standard aarch64 target; no runtime detection
//! needed) they use NEON: a two-round
//! lane-wise tournament with mask selects, then one scalar select across the
//! two lanes. Elsewhere a branch-free scalar tournament (compiled to `cmov`)
//! is used. Ties may go to either side; the tree only needs *a* minimum.

use crate::key::VACANT;
use crate::Handle;
use std::hint::select_unpredictable;

pub(crate) const FANOUT: usize = 8;

/// Winner (smallest key) of a subtree and the leaf holding it. Two full `u64`s
/// (a handle fits in the low half): writing a node is then a single 16-byte
/// store, and SIMD code may load whole nodes.
///
/// Deliberately *not* over-aligned: `IndexedHeap` embeds one (its root), and
/// Python allocates the objects embedding a heap with only 8-byte alignment
/// on 32-bit platforms. Groups, which want cache-line alignment, declare it
/// themselves and always live in `Vec`s.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(C)]
pub(crate) struct Node {
    pub(crate) key: u64,
    leaf: u64,
}

/// Winner of a subtree whose leaves are all vacant.
pub(crate) const EMPTY: Node = Node::new(VACANT, 0);

impl Node {
    #[inline(always)]
    pub(crate) const fn new(key: u64, leaf: Handle) -> Self {
        Self {
            key,
            leaf: leaf as u64,
        }
    }

    #[inline(always)]
    pub(crate) fn handle(self) -> Handle {
        self.leaf as Handle
    }
}

/// The node with the smaller key; ties keep `a`.
#[inline(always)]
pub(crate) fn winner(a: Node, b: Node) -> Node {
    let b_wins = b.key < a.key;
    Node {
        key: select_unpredictable(b_wins, b.key, a.key),
        leaf: select_unpredictable(b_wins, b.leaf, a.leaf),
    }
}

#[derive(Debug, Clone, Copy)]
#[repr(C, align(64))]
pub(crate) struct LeafGroup(pub(crate) [u64; FANOUT]);

#[derive(Debug, Clone, Copy)]
#[repr(C, align(128))]
pub(crate) struct NodeGroup(pub(crate) [Node; FANOUT]);

pub(crate) const VACANT_LEAVES: LeafGroup = LeafGroup([VACANT; FANOUT]);
pub(crate) const EMPTY_NODES: NodeGroup = NodeGroup([EMPTY; FANOUT]);

// The SIMD loads read whole nodes and groups as arrays of `u64`.
const _: () = {
    use std::mem::{align_of, offset_of, size_of};
    assert!(size_of::<Node>() == 16 && offset_of!(Node, key) == 0 && offset_of!(Node, leaf) == 8);
    assert!(size_of::<NodeGroup>() == 128 && align_of::<NodeGroup>() == 128);
    assert!(size_of::<LeafGroup>() == 64 && align_of::<LeafGroup>() == 64);
};

/// Lane value that excludes no lane from a reduction.
pub(crate) const NO_SKIP: usize = FANOUT;

/// Winner of the nodes in `group`, ignoring lane `skip`.
#[inline(always)]
pub(crate) fn group_winner(group: &NodeGroup, skip: usize) -> Node {
    #[cfg(all(
        target_arch = "aarch64",
        target_endian = "little",
        target_feature = "neon"
    ))]
    {
        // SAFETY: compiled only with the `neon` target feature; reads stay within `group`.
        unsafe { neon::group_winner(group, skip) }
    }
    #[cfg(not(all(
        target_arch = "aarch64",
        target_endian = "little",
        target_feature = "neon"
    )))]
    {
        scalar_winner(group.0, skip)
    }
}

/// Winner among the leaves of leaf group number `index`, ignoring lane `skip`.
#[inline(always)]
pub(crate) fn leaf_group_winner(leaves: &LeafGroup, index: usize, skip: usize) -> Node {
    let base = (index * FANOUT) as Handle;
    #[cfg(all(
        target_arch = "aarch64",
        target_endian = "little",
        target_feature = "neon"
    ))]
    {
        // SAFETY: compiled only with the `neon` target feature; reads stay within `leaves`.
        let (key, lane) = unsafe { neon::leaf_group_winner(leaves, skip) };
        Node::new(key, base + lane)
    }
    #[cfg(not(all(
        target_arch = "aarch64",
        target_endian = "little",
        target_feature = "neon"
    )))]
    {
        scalar_winner(
            std::array::from_fn(|j| Node::new(leaves.0[j], base + j as Handle)),
            skip,
        )
    }
}

#[cfg_attr(
    all(
        target_arch = "aarch64",
        target_endian = "little",
        target_feature = "neon"
    ),
    allow(dead_code)
)]
#[inline(always)]
fn scalar_winner(nodes: [Node; FANOUT], skip: usize) -> Node {
    let n: [Node; FANOUT] = std::array::from_fn(|j| Node {
        key: select_unpredictable(j == skip, VACANT, nodes[j].key),
        leaf: nodes[j].leaf,
    });
    winner(
        winner(winner(n[0], n[1]), winner(n[2], n[3])),
        winner(winner(n[4], n[5]), winner(n[6], n[7])),
    )
}

#[cfg(all(
    target_arch = "aarch64",
    target_endian = "little",
    target_feature = "neon"
))]
mod neon {
    use super::{LeafGroup, Node, NodeGroup};
    use core::arch::aarch64::*;
    use std::hint::select_unpredictable;

    type Pair = (uint64x2_t, uint64x2_t);

    /// Lane indices `[lo, lo + 1]`.
    #[inline(always)]
    unsafe fn lanes(lo: u64) -> uint64x2_t {
        vcombine_u64(vcreate_u64(lo), vcreate_u64(lo + 1))
    }

    /// Lane-wise winner of `(keys, payload)` pairs; ties keep `a`.
    #[inline(always)]
    unsafe fn pick(a: Pair, b: Pair) -> Pair {
        let b_wins = vcltq_u64(b.0, a.0);
        (vbslq_u64(b_wins, b.0, a.0), vbslq_u64(b_wins, b.1, a.1))
    }

    /// Final round across the two lanes: `(key, payload)`.
    #[inline(always)]
    unsafe fn finish(w: Pair) -> (u64, u64) {
        let (lo, hi) = (vgetq_lane_u64::<0>(w.0), vgetq_lane_u64::<1>(w.0));
        let hi_wins = hi < lo;
        let payload =
            select_unpredictable(hi_wins, vgetq_lane_u64::<1>(w.1), vgetq_lane_u64::<0>(w.1));
        (select_unpredictable(hi_wins, hi, lo), payload)
    }

    #[inline(always)]
    pub(super) unsafe fn group_winner(group: &NodeGroup, skip: usize) -> Node {
        let p = group.0.as_ptr().cast::<u64>();
        // Each 16-byte node is (key, leaf); vld2 splits keys from leaves.
        let (ab, cd) = (vld2q_u64(p), vld2q_u64(p.add(4)));
        let (ef, gh) = (vld2q_u64(p.add(8)), vld2q_u64(p.add(12)));
        let skip = vdupq_n_u64(skip as u64);
        // Force the skipped lane's key to VACANT (all ones) so it never wins.
        let masked = |k: uint64x2_t, lo: u64| vorrq_u64(k, vceqq_u64(lanes(lo), skip));
        let w = pick(
            pick((masked(ab.0, 0), ab.1), (masked(cd.0, 2), cd.1)),
            pick((masked(ef.0, 4), ef.1), (masked(gh.0, 6), gh.1)),
        );
        let (key, leaf) = finish(w);
        Node { key, leaf }
    }

    /// `(key, lane)` of the smallest leaf in `leaves`, ignoring lane `skip`.
    #[inline(always)]
    pub(super) unsafe fn leaf_group_winner(leaves: &LeafGroup, skip: usize) -> (u64, u32) {
        let p = leaves.0.as_ptr();
        let skip = vdupq_n_u64(skip as u64);
        let load = |lo: usize| {
            let idx = lanes(lo as u64);
            (vorrq_u64(vld1q_u64(p.add(lo)), vceqq_u64(idx, skip)), idx)
        };
        let (key, lane) = finish(pick(pick(load(0), load(2)), pick(load(4), load(6))));
        (key, lane as u32)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Few distinct keys so that ties are common.
    fn groups() -> impl Iterator<Item = [u64; FANOUT]> {
        let mut state = 0x1234_5678_9ABC_DEF0u64;
        std::iter::repeat_with(move || {
            std::array::from_fn(|_| {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                [0, 1, 2, 3, u64::MAX - 1, VACANT][(state % 6) as usize]
            })
        })
        .take(50_000)
    }

    fn reference(keys: [u64; FANOUT], skip: usize) -> u64 {
        (0..FANOUT)
            .filter(|&j| j != skip)
            .map(|j| keys[j])
            .min()
            .unwrap_or(VACANT)
    }

    #[test]
    fn group_reductions_find_a_minimum() {
        for (i, keys) in groups().enumerate() {
            let skip = i % (FANOUT + 1);
            let nodes = NodeGroup(std::array::from_fn(|j| {
                Node::new(keys[j], 100 + j as Handle)
            }));
            let w = group_winner(&nodes, skip);
            assert_eq!(w.key, reference(keys, skip), "{keys:?} skip {skip}");
            let lane = (w.handle() - 100) as usize;
            assert!(lane != skip || w.key == VACANT);
            assert_eq!(keys[lane], w.key);

            // The scalar path (used off aarch64) must agree, wherever we run.
            assert_eq!(scalar_winner(nodes.0, skip).key, w.key);

            let leaves = LeafGroup(keys);
            let w = leaf_group_winner(&leaves, 3, skip);
            assert_eq!(w.key, reference(keys, skip), "{keys:?} skip {skip}");
            let lane = w.handle() as usize - 3 * FANOUT;
            assert_eq!(keys[lane], w.key);
        }
    }
}
