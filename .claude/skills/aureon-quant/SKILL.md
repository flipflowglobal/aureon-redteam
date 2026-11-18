---
name: aureon-quant
description: Execute enterprise quantitative analysis, stochastic volatility modeling, and DeFi execution algorithms adhering to zero-toy-code standards.
---

# AUREON Quantitative Engineering Procedures

This skill provides step-by-step procedures for quantitative modeling, risk engine evaluation, and DeFi execution verification.

## 1. Zero-Toy-Code Standards

- Never generate placeholder code, stub formulas, or mock return sequences.
- All algorithms must be implemented with full mathematical rigor and explicit numerical stability checks.
- All pricing models and risk estimators must specify Big-O computational complexity.

## 2. Quantitative Model Execution Pipeline

1. **Input Verification**:
   - Ingest real or seeded price series (OHLCV) with timestamp validation.
   - Verify non-empty data arrays and positive price constraints ($S_t > 0$).
2. **Volatility & Risk Computation**:
   - Compute historical volatility and Cornish-Fisher Value at Risk (VaR).
   - Evaluate conditional drawdown and CVaR / Expected Shortfall.
3. **Execution Routing**:
   - Solve optimal execution trajectories using the Almgren-Chriss framework:
     $$x_k = X \frac{\sinh(\kappa (N - k))}{\sinh(\kappa N)}$$
   - Enforce constant product invariant checks ($x \cdot y = k$) for AMM swaps.
4. **Automated Verification**:
   - Run unit tests with tolerance bounds: `assert abs(computed - expected) < 1e-9`.
   - Confirm absence of NaN or Inf propagation across all matrix operations.
