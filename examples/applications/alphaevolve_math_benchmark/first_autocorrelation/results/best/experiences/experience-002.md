Core algorithmic mechanisms and design choices contributing to an upper bound of 1.5203482180930465 and target ratio of 0.9901021240305584 within a 950.49s evaluation budget.

- Softmax simplex parameterization: Parameterizing the non-negative sequence a via unconstrained logits through a softmax transformation enforces the simplex constraint a >= 0 and sum(a) = 1 automatically, reducing the scale-invariant autocorrelation objective to 2 * n * max(conv(a, a)).
- Tri-gear plateau-minimax optimization: Combining Adam descent on an annealed Top-K Log-Sum-Exp objective expanded over active plateau lags with hard-max subgradient polishing and periodic pair-shaving mass transport effectively prevents peak see-sawing and resolves near-tied convolution maxima.
- Multi-start width-swept arch initialization: Seeding optimization across diverse structural profiles including width-swept symmetric arch shapes pre-refined by single-step peak shaving establishes robust starting basins across the search budget.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """
    Tri-gear optimization strategy using softmax parameterization,
    Adam smooth surrogate descent, hard-max polishing, and pair-shaving.
    """
    import time
    start_time = time.time()
    time_limit = 950.0  # Safe margin for 1000s budget
    n = 600

    def get_conv(a):
        A = np.fft.rfft(a, 2 * n - 1)
        res = np.fft.irfft(A * A, 2 * n - 1)
        return np.maximum(res, 0)

    best_obj = float('inf')
    best_a = None

    def evaluate_and_save(a):
        nonlocal best_obj, best_a
        c = get_conv(a)
        obj = 2 * n * np.max(c)
        if obj < best_obj:
            best_obj = obj
            best_a = a.copy()
        return obj, c

    # Initialize diverse shapes including the width-swept arch
    x_base = np.linspace(-0.25, 0.25, n)
    base_shape = 1.0 + 4.0 * np.abs(x_base) - 16.0 * x_base**2
    base_shape = (base_shape + base_shape[::-1]) / 2
    base_shape = np.maximum(base_shape, 0) + 1e-3

    shapes = [base_shape]
    x_norm = np.linspace(-1, 1, n)
    for w in [1.0, 0.8, 0.6, 0.4]:
        shapes.append(np.maximum(1 - (x_norm / w)**2, 0) + 1e-3)
    for w in [1.0, 0.8, 0.5]:
        shapes.append(np.maximum(1 - np.abs(x_norm / w), 0) + 1e-3)
    shapes.append(np.ones(n))

    results = []
    # Phase 1: Evaluate multi-start shapes with smooth surrogate descent
    for s in shapes:
        if time.time() - start_time > time_limit:
            break
        w = np.log(s / np.sum(s))
        m = np.zeros(n)
        v = np.zeros(n)
        lr = 0.05

        for i in range(200):
            if time.time() - start_time > time_limit:
                break
            a = np.exp(w - np.max(w))
            a /= np.sum(a)
            obj, c = evaluate_and_save(a)

            c_max = np.max(c)
            exp_c = np.exp((c - c_max) / 0.01)
            idx = np.argpartition(c, -50)[-50:]
            mask = np.zeros_like(c)
            mask[idx] = 1.0
            exp_c *= mask
            sum_exp = np.sum(exp_c)
            if sum_exp > 0:
                g = 0.95 * (exp_c / sum_exp) + 0.05 / len(c)
            else:
                g = np.zeros_like(c)
                g[np.argmax(c)] = 1.0

            grad_a = 2 * np.correlate(g, a, mode='valid')
            grad_w = a * (grad_a - np.sum(a * grad_a))

            m = 0.9 * m + 0.1 * grad_w
            v = 0.999 * v + 0.001 * (grad_w ** 2)
            m_hat = m / (1 - 0.9**(i + 1))
            v_hat = v / (1 - 0.999**(i + 1))
            w = w - lr * m_hat / (np.sqrt(v_hat) + 1e-8)

        results.append((obj, w, m, v))

    if not results:
        return (np.ones(n) / n).tolist()

    # Phase 2: Refine the best shape until time budget exhausted
    results.sort(key=lambda x: x[0])
    w, m, v = results[0][1], results[0][2], results[0][3]
    lr = 0.01
    i = 200

    while time.time() - start_time < time_limit:
        a = np.exp(w - np.max(w))
        a /= np.sum(a)
        obj, c = evaluate_and_save(a)

        T = 0.01 * (1.0 - min(1.0, i / 10000)) + 1e-4

        c_max = np.max(c)
        exp_c = np.exp((c - c_max) / T)
        idx = np.argpartition(c, -50)[-50:]
        mask = np.zeros_like(c)
        mask[idx] = 1.0
        exp_c *= mask
        sum_exp = np.sum(exp_c)
        if sum_exp > 0:
            g = 0.95 * (exp_c / sum_exp) + 0.05 / len(c)
        else:
            g = np.zeros_like(c)
            g[np.argmax(c)] = 1.0

        grad_a = 2 * np.correlate(g, a, mode='valid')
        grad_w = a * (grad_a - np.sum(a * grad_a))

        m = 0.9 * m + 0.1 * grad_w
        v = 0.999 * v + 0.001 * (grad_w ** 2)
        m_hat = m / (1 - 0.9**(i + 1))
        v_hat = v / (1 - 0.999**(i + 1))
        w = w - lr * m_hat / (np.sqrt(v_hat) + 1e-8)

        # Hard-max polishing
        if i % 100 == 0:
            for _ in range(5):
                a_polish = np.exp(w - np.max(w))
                a_polish /= np.sum(a_polish)
                c_polish = get_conv(a_polish)
                max_idx = np.argmax(c_polish)
                g_hard = np.zeros_like(c_polish)
                g_hard[max_idx] = 1.0
                grad_a_hard = 2 * np.correlate(g_hard, a_polish, mode='valid')
                grad_w_hard = a_polish * (grad_a_hard - np.sum(a_polish * grad_a_hard))

                w_new = w - (lr * 0.5) * grad_w_hard
                a_new = np.exp(w_new - np.max(w_new))
                a_new /= np.sum(a_new)
                c_new = get_conv(a_new)
                obj_new = 2 * n * np.max(c_new)

                if obj_new < 2 * n * np.max(c_polish):
                    w = w_new
                    if obj_new < best_obj:
                        best_obj = obj_new
                        best_a = a_new.copy()
                else:
                    break

        # Pair-shaving mass transport
        if i % 150 == 0:
            a_shave = np.exp(w - np.max(w))
            a_shave /= np.sum(a_shave)
            c_shave = get_conv(a_shave)
            max_idx = np.argmax(c_shave)

            start_j = max(0, max_idx - n + 1)
            end_j = min(n, max_idx + 1)
            if start_j < end_j:
                j_arr = np.arange(start_j, end_j)
                k_arr = max_idx - j_arr
                prods = a_shave[j_arr] * a_shave[k_arr]
                best_idx = np.argmax(prods)

                if prods[best_idx] > 0:
                    j = j_arr[best_idx]
                    k = k_arr[best_idx]

                    g_hard = np.zeros_like(c_shave)
                    g_hard[max_idx] = 1.0
                    grad_a_hard = 2 * np.correlate(g_hard, a_shave, mode='valid')
                    min_idx = np.argmin(grad_a_hard)

                    w_new = w.copy()
                    w_new[j] -= 0.05
                    w_new[k] -= 0.05
                    w_new[min_idx] += 0.05

                    a_new = np.exp(w_new - np.max(w_new))
                    a_new /= np.sum(a_new)
                    c_new = get_conv(a_new)
                    obj_new = 2 * n * np.max(c_new)

                    if obj_new < 2 * n * np.max(c_shave):
                        w = w_new
                        if obj_new < best_obj:
                            best_obj = obj_new
                            best_a = a_new.copy()

        i += 1

    return best_a.tolist() if best_a is not None else (np.ones(n) / n).tolist()
# EVOLVE_END


def evaluate_sequence(sequence: list[float]) -> float:
    if not isinstance(sequence, list) or not sequence:
        return np.inf
    for value in sequence:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return np.inf
        if np.isnan(value) or np.isinf(value):
            return np.inf
    sequence = [min(1000.0, max(0.0, float(value))) for value in sequence]
    total = np.sum(sequence)
    if total < 0.01:
        return np.inf
    convolution = np.convolve(sequence, sequence)
    return float(2 * len(sequence) * max(convolution) / total**2)


def run_search_for_best_sequence():
    return list(search_for_best_sequence())


if __name__ == "__main__":
    print(json.dumps({"sequence": run_search_for_best_sequence()}))

```
