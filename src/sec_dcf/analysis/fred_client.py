"""Lightweight FRED API client for risk-free rate observations (e.g. DGS10)."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from sec_dcf.exceptions import FredApiError
from sec_dcf.models.analysis import RiskFreeRateQuote

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_TIMEOUT_SECONDS = 10.0
PERCENT_DIVISOR = Decimal("100")

_API_KEY_PARAM_RE = re.compile(r"(api_key=)[^&\s\"']+")


def _mask_api_key(message: str, api_key: str | None) -> str:
    """Redact any FRED API key occurrences from error messages or URLs."""
    masked = _API_KEY_PARAM_RE.sub(r"\1***", message)
    if api_key:
        masked = masked.replace(api_key, "***")
    return masked


def _default_http_get(url: str, timeout: float) -> str:
    """Execute a synchronous GET request using stdlib urllib with explicit timeout."""
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "sec-dcf-audit-engine"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


class FredClient:
    """Minimal, dependency-free FRED API client reading FRED_API_KEY from environment."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        http_get: Callable[[str, float], str] | None = None,
    ) -> None:
        if timeout <= 0:
            raise FredApiError(f"FRED HTTP timeout must be positive, got {timeout}.")
        if api_key is None:
            load_dotenv()
            api_key = os.getenv("FRED_API_KEY", "").strip()
        else:
            api_key = api_key.strip()

        if not api_key:
            raise FredApiError(
                "FRED_API_KEY is missing. Set FRED_API_KEY in your environment/.env "
                "or pass risk_free_rate manually for offline calculations."
            )

        self._api_key = api_key
        self._timeout = timeout
        self._http_get = http_get or _default_http_get

    def get_latest_rate(self, series_id: str = "DGS10") -> RiskFreeRateQuote:
        """Fetch the most recent non-missing observation for a FRED interest-rate series."""
        cleaned_series = series_id.strip().upper()
        if not cleaned_series:
            raise FredApiError("FRED series_id must be non-empty.")

        query = urlencode(
            {
                "series_id": cleaned_series,
                "api_key": self._api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": "10",
            }
        )
        url = f"{FRED_OBSERVATIONS_URL}?{query}"

        try:
            raw_body = self._http_get(url, self._timeout)
        except HTTPError as exc:
            safe_msg = _mask_api_key(f"HTTP {exc.code}: {exc.reason}", self._api_key)
            raise FredApiError(
                f"FRED API request failed for series '{cleaned_series}' ({safe_msg})."
            ) from None
        except (URLError, TimeoutError, OSError) as exc:
            safe_msg = _mask_api_key(str(exc), self._api_key)
            raise FredApiError(
                f"FRED API network/timeout error for series '{cleaned_series}': {safe_msg}"
            ) from None
        except Exception as exc:  # noqa: BLE001
            safe_msg = _mask_api_key(str(exc), self._api_key)
            raise FredApiError(
                f"Unexpected error querying FRED series '{cleaned_series}': {safe_msg}"
            ) from None

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise FredApiError(
                f"FRED API returned invalid JSON for series '{cleaned_series}': {exc.msg}"
            ) from None

        observations = payload.get("observations")
        if not isinstance(observations, list):
            raise FredApiError(
                f"FRED API payload for '{cleaned_series}' missing 'observations' list."
            )

        for obs in observations:
            if not isinstance(obs, dict):
                continue
            raw_val = str(obs.get("value", "")).strip()
            if not raw_val or raw_val == ".":
                continue
            try:
                pct_val = Decimal(raw_val)
            except InvalidOperation:
                continue

            raw_date = obs.get("date")
            obs_date: date | None = None
            if isinstance(raw_date, str) and raw_date.strip():
                try:
                    obs_date = date.fromisoformat(raw_date.strip())
                except ValueError:
                    obs_date = None

            return RiskFreeRateQuote(
                series_id=cleaned_series,
                rate=pct_val / PERCENT_DIVISOR,
                observation_date=obs_date,
                source="fred",
            )

        raise FredApiError(
            f"FRED series '{cleaned_series}' returned no valid numeric observations."
        )


def fetch_risk_free_rate(
    series_id: str = "DGS10",
    *,
    api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> RiskFreeRateQuote:
    """Convenience wrapper to fetch the latest risk-free rate from FRED."""
    client = FredClient(api_key=api_key, timeout=timeout)
    return client.get_latest_rate(series_id=series_id)
