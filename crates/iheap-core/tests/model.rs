//! Randomized differential test against a `HashMap` model.

use iheap_core::{Handle, IndexedHeap, Order};
use std::collections::HashMap;

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
        [f64::NEG_INFINITY, -0.0, 0.0, f64::INFINITY][self.below(4) as usize].min(
            if self.below(4) == 0 {
                f64::INFINITY
            } else {
                self.below(50) as f64 - 25.0
            },
        )
    }
}

macro_rules! model_test {
    ($name:ident, $ty:ident) => {
        #[test]
        fn $name() {
            for (seed, order) in [(1, Order::Min), (2, Order::Max), (3, Order::Min)] {
                let mut rng = XorShift(0x9E37_79B9_7F4A_7C15 ^ seed);
                let mut heap = $ty::new(order);
                let mut model: HashMap<Handle, f64> = HashMap::new();
                let precedes = |a: f64, b: f64| match order {
                    Order::Min => a < b,
                    Order::Max => a > b,
                };
                for step in 0..8_000 {
                    let live: Vec<Handle> = model.keys().copied().collect();
                    let pick = |rng: &mut XorShift| live[rng.below(live.len() as u64) as usize];
                    // Drift between growing and shrinking phases.
                    let grow = (step / 1_000) % 2 == 0;
                    match rng.below(10) {
                        0..=2 if grow || live.is_empty() => {
                            let p = rng.priority();
                            let h = heap.push(p).unwrap();
                            assert!(model.insert(h, p).is_none());
                        }
                        3 => {
                            let ps: Vec<f64> = (0..rng.below(40)).map(|_| rng.priority()).collect();
                            let hs = heap.push_many(&ps).unwrap();
                            for (h, p) in hs.into_iter().zip(ps) {
                                assert!(model.insert(h, p).is_none());
                            }
                        }
                        4 | 5 => match heap.pop() {
                            Some((h, p)) => {
                                assert_eq!(model.remove(&h).map(f64::to_bits), Some(p.to_bits()));
                                assert!(model.values().all(|&q| !precedes(q, p)));
                            }
                            None => assert!(model.is_empty()),
                        },
                        6 if !live.is_empty() => {
                            let h = pick(&mut rng);
                            assert_eq!(
                                heap.remove(h).unwrap().to_bits(),
                                model.remove(&h).unwrap().to_bits()
                            );
                            assert!(!heap.contains(h) && heap.remove(h).is_err());
                        }
                        7 | 8 if !live.is_empty() => {
                            let h = pick(&mut rng);
                            let p = rng.priority();
                            let old = heap.update(h, p).unwrap();
                            assert_eq!(old.to_bits(), model.insert(h, p).unwrap().to_bits());
                        }
                        9 if !live.is_empty() => {
                            let k = rng.below(live.len() as u64 + 1) as usize;
                            let hs: Vec<Handle> = (0..k).map(|_| pick(&mut rng)).collect();
                            let ps: Vec<f64> = (0..k).map(|_| rng.priority()).collect();
                            heap.update_many(&hs, &ps).unwrap();
                            for (h, p) in hs.into_iter().zip(ps) {
                                model.insert(h, p);
                            }
                        }
                        _ => {
                            if rng.below(1_000) == 0 {
                                heap.clear();
                                model.clear();
                            }
                        }
                    }
                    assert_eq!(heap.len(), model.len());
                    if let Some((h, p)) = heap.peek() {
                        assert_eq!(model[&h].to_bits(), p.to_bits());
                        assert!(model.values().all(|&q| !precedes(q, p)));
                    }
                    if let Some(pp) = heap.push_pop(rng.priority()).unwrap() {
                        // Handle reused for the pushed priority; re-sync model.
                        let (h, old) = pp;
                        assert_eq!(model[&h].to_bits(), old.to_bits());
                        model.insert(h, heap.priority(h).unwrap());
                    }
                }
                let mut got: Vec<(Handle, u64)> =
                    heap.iter().map(|(h, p)| (h, p.to_bits())).collect();
                let mut want: Vec<(Handle, u64)> =
                    model.iter().map(|(&h, p)| (h, p.to_bits())).collect();
                got.sort_unstable();
                want.sort_unstable();
                assert_eq!(got, want);
            }
        }
    };
}

model_test!(matches_model_under_random_operations, IndexedHeap);
