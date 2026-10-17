//! Core-only micro-benchmark (no Python): `cargo run --release --example bench -p iheap-core`.
//!
//! Each phase is repeated (with untimed setup) until at least `MIN_TOTAL` has
//! been spent timing it, and the fastest repetition is reported, so that short
//! phases are not distorted by CPU frequency ramp-up or core migration.

use iheap_core::{Handle, IndexedHeap, Order};
use std::hint::black_box;
use std::time::{Duration, Instant};

const MIN_TOTAL: Duration = Duration::from_millis(200);
const MIN_REPS: usize = 5;

struct XorShift(u64);

impl XorShift {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }
    fn unit(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
}

fn time<S>(label: &str, ops: usize, mut setup: impl FnMut() -> S, mut run: impl FnMut(S)) {
    let (mut best, mut total, mut reps) = (Duration::MAX, Duration::ZERO, 0);
    while reps < MIN_REPS || total < MIN_TOTAL {
        let state = setup();
        let t = Instant::now();
        run(state);
        let elapsed = t.elapsed();
        best = best.min(elapsed);
        total += elapsed;
        reps += 1;
    }
    print!("  {label} {:>6.1}", best.as_nanos() as f64 / ops as f64);
}

macro_rules! suite {
    ($ty:ident, $n:expr, $prios:expr, $updates:expr, $deletes:expr) => {{
        let (n, prios, updates, deletes) = ($n, $prios, $updates, $deletes);
        let built = || {
            let mut h = $ty::new(Order::Min);
            h.push_many(prios).unwrap();
            h
        };
        print!("{:<15} n={n:<9}", stringify!($ty));
        time(
            "insert",
            n,
            || (),
            |()| {
                let mut h = $ty::new(Order::Min);
                for &p in prios {
                    black_box(h.push(p).unwrap());
                }
                black_box(h);
            },
        );
        time("build", n, || (), |()| drop(black_box(built())));
        time("update", n, built, |mut h| {
            for &(handle, p) in updates {
                black_box(h.update(handle, p).unwrap());
            }
        });
        time("delete", n / 2, built, |mut h| {
            for &handle in deletes {
                black_box(h.remove(handle).unwrap());
            }
        });
        time("pop", n, built, |mut h| {
            while let Some(x) = h.pop() {
                black_box(x);
            }
        });
        println!();
    }};
}

fn main() {
    println!("ns/op (best of repeated runs)");
    for n in [10_000usize, 100_000, 1_000_000] {
        let mut rng = XorShift(0x2545_F491_4F6C_DD1D);
        let prios: Vec<f64> = (0..n).map(|_| rng.unit()).collect();
        let updates: Vec<(Handle, f64)> = (0..n)
            .map(|_| ((rng.next() % n as u64) as Handle, rng.unit()))
            .collect();
        let mut deletes: Vec<Handle> = (0..n as Handle).collect();
        for i in (1..n).rev() {
            deletes.swap(i, (rng.next() % (i as u64 + 1)) as usize);
        }
        deletes.truncate(n / 2);
        suite!(IndexedHeap, n, &prios, &updates, &deletes);
    }
}
