"""One explicit activation for runtime and local retrieval experiments."""
import math

SCORE_VERSION = "logit-single-sigmoid/1"
LEGACY_SCORE_VERSION = "legacy-double-sigmoid/1"


def sigmoid(logit: float) -> float:
    if not math.isfinite(logit):
        raise ValueError("reranker 返回非有限 logit")
    return 1 / (1 + math.exp(-logit)) if logit >= 0 else math.exp(logit) / (1 + math.exp(logit))


def threshold_on_single_scale(value: float) -> float:
    """Exact unit conversion, NOT fitting or relaxing a calibrated policy.

    Old thresholds were calibrated on sigmoid(sigmoid(logit)). Remove only
    that outer activation: compare sigmoid(logit) against logit(threshold).
    Values outside the outer sigmoid's achievable range stay always/never.
    """
    if not 0 < value < 1:
        raise ValueError("非法历史阈值")
    return math.log(value / (1 - value))


def predict_scores(model, pairs):
    from torch.nn import Identity
    from agent.core.request_control import check_cancelled, is_controlled
    check_cancelled()
    batches = [[pair] for pair in pairs] if is_controlled() else [pairs]
    logits = []
    for batch in batches:
        check_cancelled()
        raw = model.predict(batch, activation_fn=Identity(), batch_size=1)
        logits.extend([float(v) for v in raw.reshape(-1)] if hasattr(raw, "reshape") else list(map(float, raw)))
    if len(logits) != len(pairs):
        raise ValueError("reranker 返回数量与输入不符")
    return [(value, sigmoid(value)) for value in logits]
