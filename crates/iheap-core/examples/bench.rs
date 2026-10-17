//! Core-only micro-benchmark (no Python): `cargo run --release --example bench -p iheap-core`.

use iheap_core::{Handle, IndexedHeap, Order};
use std::hint::black_box;
use std::time::Instant;

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

fn time(label: &str, ops: usize, mut f: impl FnMut()) {
    let best = (0..5)
        .map(|_| {
            let t = Instant::now();
            f();
            t.elapsed()
        })
        .min()
        .unwrap();
    println!(
        "  {label:<8} {:>7.1} ns/op",
        best.as_nanos() as f64 / ops as f64
    );
}

fn main() {
    for n in [100_000usize, 1_000_000] {
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
        let built = || {
            let mut h = IndexedHeap::new(Order::Min);
            h.push_many(&prios).unwrap();
            h
        };

        println!("n = {n}");
        time("insert", n, || {
            let mut h = IndexedHeap::new(Order::Min);
            for &p in &prios {
                black_box(h.push(p).unwrap());
            }
        });
        time("build", n, || drop(black_box(built())));
        let mut h = built();
        time("update", n, || {
            for &(handle, p) in &updates {
                black_box(h.update(handle, p).unwrap());
            }
        });
        let fresh: Vec<_> = (0..5).map(|_| built()).collect();
        let mut it = fresh.into_iter();
        time("delete", n / 2, || {
            let mut h = it.next().unwrap();
            for &handle in &deletes {
                black_box(h.remove(handle).unwrap());
            }
        });
        let fresh: Vec<_> = (0..5).map(|_| built()).collect();
        let mut it = fresh.into_iter();
        time("pop", n, || {
            let mut h = it.next().unwrap();
            while let Some(x) = h.pop() {
                black_box(x);
            }
        });
    }
}
