# verify_extension.py
import time
import random
import numpy as np

# Standardized Curve Parameters (y^2 = x^3 + x + 3 mod 1000000007)
P_PRIME = 1000000007
CURVE_A = 1
CURVE_B = 3

# w-NAF signed decomposition helper
def w_naf(scalar, w):
    naf = []
    i = 0
    temp = scalar
    while temp > 0:
        if temp % 2 != 0:
            digit = temp % (2**w)
            if digit >= 2**(w-1):
                digit -= 2**w
            temp -= digit
            naf.append((i, digit))
            temp //= 2**w
            i += w
        else:
            temp //= 2
            i += 1
    return naf

# Flat Bitmask Builders
def build_pippenger_bitmask(scalars, N, K, c):
    digits = np.zeros((K, N), dtype=np.int8)
    for i in range(N):
        scalar = scalars[i]
        for j in range(K):
            digits[j, i] = (scalar >> (j * c)) & ((1 << c) - 1)
    return digits

def build_yggdrasil_bitmask(scalars, N, K, w, c):
    rem = np.full((K, N), -1, dtype=np.int8)
    digit = np.zeros((K, N), dtype=np.int8)
    for i in range(N):
        scalar = scalars[i]
        terms = w_naf(scalar, w)
        for bit_index, d in terms:
            q = bit_index // c
            r = bit_index % c
            if q >= K:
                continue
            rem[q, i] = r
            digit[q, i] = d
    return rem, digit

def generate_base_points(N, p, a_coeff, b_coeff):
    points = []
    x = 2
    while len(points) < N:
        v = (x**3 + a_coeff*x + b_coeff) % p
        if pow(v, (p - 1) // 2, p) == 1:
            y = pow(v, (p + 1) // 4, p)
            points.append((x, y, 1))
        x += 1
    return points

def jacobian_to_affine(x, y, z, p):
    if z == 0:
        return 0, 0
    z_inv = pow(int(z), p - 2, p)
    z_inv2 = (z_inv * z_inv) % p
    z_inv3 = (z_inv2 * z_inv) % p
    return (x * z_inv2) % p, (y * z_inv3) % p

def points_equal(pt1, pt2, p):
    ax1, ay1 = jacobian_to_affine(pt1[0], pt1[1], pt1[2], p)
    ax2, ay2 = jacobian_to_affine(pt2[0], pt2[1], pt2[2], p)
    return ax1 == ax2 and ay1 == ay2

def main():
    print("=" * 80)
    print("      YGGDRASIL SECURE RUST ENGINE - BENCHMARK VERIFICATION RUNNER")
    print("=" * 80)
    
    # Try importing the compiled Rust module
    try:
        import yggdrasil_msm_core
        print("[OK] Rust binary extension module loaded successfully!")
    except ImportError as e:
        print("[ERROR] Failed to import compiled Rust module.")
        print("Please ensure that you have yggdrasil_msm_core.so (Linux) or")
        print("yggdrasil_msm_core.pyd (Windows) in the same directory.")
        print(f"Details: {e}")
        return

    # Define test scale (N=4096 and N=1M/4M for production)
    scales = [
        (4096, 128, 5, 7, 5, "Config B (Standard scale)"),
        (1048576, 128, 5, 7, 5, "Production Scale E (1 Million points)"),
    ]

    for N, bits, c_pip, w_ygg, c_ygg, label in scales:
        print(f"\nRunning Scenario: {label} (N={N}, {bits}-bit scalar)...")
        print("Generating mock dataset...")
        random.seed(1337)
        scalars = [random.randint(1, (1 << bits) - 1) for _ in range(N)]
        base_points = generate_base_points(N, P_PRIME, CURVE_A, CURVE_B)

        base_x = np.array([pt[0] for pt in base_points], dtype=np.int64)
        base_y = np.array([pt[1] for pt in base_points], dtype=np.int64)
        base_z = np.array([pt[2] for pt in base_points], dtype=np.int64)

        # Build Bitmasks
        print("Constructing Flat Bitmask matrices...")
        K_pip = (bits + c_pip - 1) // c_pip
        K_ygg = (bits + w_ygg + c_ygg - 1) // c_ygg

        pip_digits = build_pippenger_bitmask(scalars, N, K_pip, c_pip)
        ygg_rem, ygg_digit = build_yggdrasil_bitmask(scalars, N, K_ygg, w_ygg, c_ygg)

        # Enforce C-contiguous layouts
        pip_digits = np.ascontiguousarray(pip_digits, dtype=np.int8)
        ygg_rem = np.ascontiguousarray(ygg_rem, dtype=np.int8)
        ygg_digit = np.ascontiguousarray(ygg_digit, dtype=np.int8)

        # Extract raw memory pointers
        base_x_ptr = base_x.ctypes.data
        base_y_ptr = base_y.ctypes.data
        base_z_ptr = base_z.ctypes.data
        pip_digits_ptr = pip_digits.ctypes.data
        ygg_rem_ptr = ygg_rem.ctypes.data
        ygg_digit_ptr = ygg_digit.ctypes.data

        # 1. Run Pippenger via Rust FFI
        print("Invoking Rust Pippenger FFI execution...")
        start_t = time.perf_counter()
        res_pip = yggdrasil_msm_core.run_pippenger_ffi(
            base_x_ptr, base_y_ptr, base_z_ptr, pip_digits_ptr, N, K_pip, c_pip, CURVE_A
        )
        pip_time = time.perf_counter() - start_t
        print(f"  Pippenger Execution Time: {pip_time:.4f}s")

        # 2. Run Yggdrasil via Rust FFI
        print("Invoking Rust Yggdrasil FFI execution...")
        start_t = time.perf_counter()
        res_ygg = yggdrasil_msm_core.run_yggdrasil_ffi(
            base_x_ptr, base_y_ptr, base_z_ptr, ygg_rem_ptr, ygg_digit_ptr, N, K_ygg, w_ygg, c_ygg, CURVE_A
        )
        ygg_time = time.perf_counter() - start_t
        print(f"  Yggdrasil Execution Time: {ygg_time:.4f}s")

        # Verify results match
        p1 = (res_pip[0][0], res_pip[1][0], res_pip[2][0])
        p2 = (res_ygg[0][0], res_ygg[1][0], res_ygg[2][0])
        
        match = points_equal(p1, p2, P_PRIME)
        speedup = pip_time / ygg_time if ygg_time > 0 else 0

        print(f"  Pippenger Result (Affine): {jacobian_to_affine(*p1, P_PRIME)}")
        print(f"  Yggdrasil Result (Affine): {jacobian_to_affine(*p2, P_PRIME)}")
        print(f"  Mathematical Status: {'[OK] MATCHED' if match else '[FAILED] MISMATCH'}")
        print(f"  Speedup Factor: {speedup:.2f}x")

if __name__ == '__main__':
    main()
