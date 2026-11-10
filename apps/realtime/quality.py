
"""Evaluate client-reported network quality for V2-061."""

# Provisional values pending confirmation by the tech lead.
MINIMUM_QUALITY_THRESHOLDS = {
    "latency_ms": 300,
    "jitter_ms": 30,
    "packet_loss_percent": 3,
    "upload_bandwidth_kbps": 500,
    "download_bandwidth_kbps": 1000,
}


def assess_connection_quality(metrics):
    """Evaluate whether all submitted network measurements meet the minimum."""
    failed_metrics = []

    for metric, threshold in MINIMUM_QUALITY_THRESHOLDS.items():
        value = metrics[metric]

        if metric in ("upload_bandwidth_kbps", "download_bandwidth_kbps"):
            meets_requirement = value >= threshold
        else:
            meets_requirement = value <= threshold

        if not meets_requirement:
            failed_metrics.append(metric)

    passed = not failed_metrics

    return {
        "quality": "good" if passed else "below_minimum",
        "passed": passed,
        "failed_metrics": failed_metrics,
        "message": (
            "Your connection meets the minimum requirements."
            if passed
            else "Your connection does not meet all minimum requirements."
        ),
        "metrics": metrics,
    }
