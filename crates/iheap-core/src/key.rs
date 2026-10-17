//! Order-preserving encoding of `f64` priorities as `u64` keys.
//!
//! Integer keys give a total order (so `-0.0` and `+0.0` are distinct and
//! round-trip exactly), compare with plain integer instructions, and leave
//! room for a sentinel: [`VACANT`] is the image of a NaN, which is rejected,
//! so it can never collide with a real priority and it loses every comparison.

use crate::{Error, Order, Result};

/// Key of a free leaf; larger than every real key.
pub(crate) const VACANT: u64 = u64::MAX;

/// Flip all bits of negative floats and set the sign bit of positive ones:
/// unsigned integer order then matches float order.
#[inline(always)]
fn encode_min(p: f64) -> u64 {
    let bits = p.to_bits();
    if bits >> 63 == 1 {
        !bits
    } else {
        bits | 1 << 63
    }
}

#[inline(always)]
fn decode_min(key: u64) -> f64 {
    f64::from_bits(if key >> 63 == 1 {
        key & !(1 << 63)
    } else {
        !key
    })
}

/// Encodes `priority` so that smaller keys are popped first under `order`.
#[inline(always)]
pub(crate) fn encode(priority: f64, order: Order) -> Result<u64> {
    if priority.is_nan() {
        return Err(Error::NanPriority);
    }
    let key = encode_min(priority);
    Ok(match order {
        Order::Min => key,
        Order::Max => !key,
    })
}

#[inline(always)]
pub(crate) fn decode(key: u64, order: Order) -> f64 {
    decode_min(match order {
        Order::Min => key,
        Order::Max => !key,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const ASCENDING: [f64; 10] = [
        f64::NEG_INFINITY,
        -1e300,
        -1.0,
        -f64::MIN_POSITIVE,
        -0.0,
        0.0,
        f64::MIN_POSITIVE,
        1.0,
        1e300,
        f64::INFINITY,
    ];

    #[test]
    fn preserves_order_and_round_trips() {
        for order in [Order::Min, Order::Max] {
            let keys: Vec<u64> = ASCENDING
                .iter()
                .map(|&p| encode(p, order).unwrap())
                .collect();
            for w in keys.windows(2) {
                match order {
                    Order::Min => assert!(w[0] < w[1]),
                    Order::Max => assert!(w[0] > w[1]),
                }
            }
            for (&p, &k) in ASCENDING.iter().zip(&keys) {
                assert_ne!(k, VACANT);
                assert_eq!(decode(k, order).to_bits(), p.to_bits());
            }
        }
    }

    #[test]
    fn rejects_nan() {
        assert_eq!(encode(f64::NAN, Order::Min), Err(Error::NanPriority));
        assert_eq!(encode(-f64::NAN, Order::Max), Err(Error::NanPriority));
    }
}
