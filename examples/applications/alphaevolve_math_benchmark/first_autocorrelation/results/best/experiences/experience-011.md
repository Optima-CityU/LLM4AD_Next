Key design mechanisms enabling effective optimization of non-negative autoconvolution bounds on the L1-simplex.

- Symmetry-breaking arch seed initialization: Sweeping the shape width parameter across [0.7, 1.3] with slight asymmetric uniform noise breaks symmetry traps early and delivers superior starting configurations prior to gradient descent.
- Adaptive plateau-union surrogate and inverse-pressure tail: Dynamically scaling Top-K log-sum-exp active sets with the plateau width and blending a 10% true inverse-pressure tail prevents see-saw oscillations across competing autoconvolution peaks during Adam optimization.
- Target-metric mass transport refinement: Alternating smooth L1-simplex gradient updates with acceptance-tested mass transport from high-contributing pairs to indices minimizing the composite metric (contribution + 0.1 * current_mass) reliably lowers the exact non-differentiable maximum.

```python
#!/usr/bin/env python3
"""Initial step function for the first autocorrelation inequality."""

import json

import numpy as np


# EVOLVE_START
def search_for_best_sequence():
    """Generate optimized step function using refined boundary-focused approach."""
    import time
    start_time = time.time()
    dimension = 600
    n = dimension
    best_obj = float('inf')
    best_a = None
    
    def get_obj(seq):
        if np.sum(seq) < 1e-6:
            return float('inf')
        conv = np.convolve(seq, seq)
        return 2 * n * np.max(conv) / (np.sum(seq)**2)

    time_limit = 950.0
    
    # 1. Width-swept arch seed initialization (scanning widths in [0.7, 1.3])
    widths = np.linspace(0.7, 1.3, 31)
    best_seed = None
    best_seed_obj = float('inf')
    
    for width in widths:
        x = np.linspace(-width/2, width/2, dimension)
        # Arch function
        arch = 1.0 + 4.0 * np.abs(x) - 16.0 * x**2
        # Remove hard symmetry constraints; add slight asymmetry to allow asymmetric profiles
        arch += np.random.uniform(-1e-5, 1e-5, dimension)
        arch = np.maximum(arch, 1e-12)
        arch /= np.sum(arch)
        
        obj = get_obj(arch)
        if obj < best_seed_obj:
            best_seed_obj = obj
            best_seed = arch.copy()
            
    # 2. Initial acceptance-tested peak-shaving step on the best seed
    a = best_seed.copy()
    current_obj = get_obj(a)
    for _ in range(50):
        b = np.convolve(a, a)
        max_idx = np.argmax(b)
        subgrad_a = np.zeros(n)
        for i in range(n):
            if 0 <= max_idx - i < n:
                subgrad_a[i] = 2 * a[max_idx - i]
        
        step = 1e-3
        improved = False
        for _ in range(10):
            a_new = a - step * subgrad_a
            a_new = np.maximum(a_new, 1e-12)
            a_new /= np.sum(a_new)
            obj_new = get_obj(a_new)
            if obj_new < current_obj:
                a = a_new
                current_obj = obj_new
                improved = True
                break
            step *= 0.5
        if not improved:
            break
            
    # Track the globally best sequence
    if current_obj < best_obj:
        best_obj = current_obj
        best_a = a.copy()
        
    # Softmax parameterization logits
    w = np.log(a)
    
    # Adam optimizer parameters
    m = np.zeros_like(w)
    v = np.zeros_like(w)
    beta1 = 0.9
    beta2 = 0.999
    lr = 0.05
    
    iteration = 0
    
    # 3. Main optimization phase
    while time.time() - start_time < time_limit:
        iteration += 1
        
        w_max = np.max(w)
        exp_w = np.exp(w - w_max)
        a = exp_w / np.sum(exp_w)
        
        # Continuous tracking of globally best sequence
        if iteration % 20 == 1:
            obj = get_obj(a)
            if obj < best_obj:
                best_obj = obj
                best_a = a.copy()
        
        # FFT for O(n log n) convolution
        A = np.fft.rfft(a, 2*n-1)
        b = np.fft.irfft(A * A, 2*n-1)
        
        # Plateau-union active set (Top-K LSE surrogate)
        # K scales dynamically with plateau width
        max_b = np.max(b)
        margin = abs(max_b) * 0.005
        idx = np.where(b >= max_b - margin)[0]
        if len(idx) < 20:
            idx = np.argpartition(b, -20)[-20:]
        
        top_b = b[idx]
        
        # Aggressive temperature annealing
        progress = min(1.0, (time.time() - start_time) / time_limit)
        tau = max_b * (10 ** (-2.0 - 4.0 * progress)) + 1e-12
        
        exp_b = np.exp((top_b - np.max(top_b)) / tau)
        sum_exp = np.sum(exp_b)
        
        p = np.zeros_like(b)
        p[idx] = exp_b / sum_exp
        
        # True inverse-pressure tail blended at 10% to keep non-active peaks suppressed
        tail = b / np.sum(b)
        p = 0.9 * p + 0.1 * tail
        
        # FFT for O(n log n) correlation
        P = np.fft.rfft(p, 2*n-1)
        grad_a = 2 * np.fft.irfft(P * np.conj(A), 2*n-1)[:n]
        
        # Jacobian of softmax
        sum_a_grad_a = np.sum(a * grad_a)
        grad_w = a * (grad_a - sum_a_grad_a)
        
        # Smooth gradient descent using Adam
        m = beta1 * m + (1 - beta1) * grad_w
        v = beta2 * v + (1 - beta2) * (grad_w**2)
        m_hat = m / (1 - beta1**iteration)
        v_hat = v / (1 - beta2**iteration)
        
        w -= lr * m_hat / (np.sqrt(v_hat) + 1e-8)
        
        # Non-smooth refinements
        if iteration % 100 == 0:
            current_obj = get_obj(a)
            
            # Hard-max projected subgradients
            max_idx = np.argmax(b)
            subgrad_a = np.zeros(n)
            for i in range(n):
                if 0 <= max_idx - i < n:
                    subgrad_a[i] = 2 * a[max_idx - i]
            
            subgrad_w = a * (subgrad_a - np.sum(a * subgrad_a))
            
            # Backtracking line search
            step = 1.0
            for _ in range(5):
                w_new = w - step * subgrad_w
                exp_w_new = np.exp(w_new - np.max(w_new))
                a_new = exp_w_new / np.sum(exp_w_new)
                obj_new = get_obj(a_new)
                # Accepted only if the true objective strictly decreases
                if obj_new < current_obj:
                    w = w_new
                    current_obj = obj_new
                    if obj_new < best_obj:
                        best_obj = obj_new
                        best_a = a_new.copy()
                    break
                step *= 0.5
            
            # Multi-pair mass transport
            a_current = np.exp(w - np.max(w))
            a_current /= np.sum(a_current)
            
            b_current = np.convolve(a_current, a_current)
            max_idx = np.argmax(b_current)
            
            contributions = np.zeros(n)
            for i in range(n):
                if 0 <= max_idx - i < n:
                    contributions[i] = a_current[i] * a_current[max_idx - i]
            
            # Shift mass from top-contributing index pairs to indices with the lowest target metric
            target_metric = contributions + 0.1 * a_current
            top_indices = np.argsort(contributions)[-5:]
            low_indices = np.argsort(target_metric)[:5]
            
            improved = False
            for top_i in top_indices:
                for low_i in low_indices:
                    if top_i != low_i:
                        shift = a_current[top_i] * 0.05
                        a_transport = a_current.copy()
                        a_transport[top_i] -= shift
                        a_transport[low_i] += shift
                        a_transport = np.maximum(a_transport, 1e-12)
                        a_transport /= np.sum(a_transport)
                        
                        obj_transport = get_obj(a_transport)
                        if obj_transport < current_obj:
                            w = np.log(a_transport)
                            current_obj = obj_transport
                            if obj_transport < best_obj:
                                best_obj = obj_transport
                                best_a = a_transport.copy()
                            improved = True
                            break
                if improved:
                    break

    if best_a is None:
        best_a = np.ones(dimension) / dimension
        
    return best_a.tolist()
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
