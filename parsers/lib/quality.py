"""
This library contains validation functions applied to all parsers by the feeder.
This is a higher level validation than validation.py
"""
from datetime import datetime, timezone
from typing import Any
from warnings import warn

import arrow

from electricitymap.contrib.config import EXCHANGES_CONFIG, emission_factors
from electricitymap.contrib.lib.types import ZoneKey


class ValidationError(ValueError):
    pass


def validate_datapoint_format(datapoint: dict[str, Any], kind: str, zone_key: ZoneKey):
    """
    Checks that a datapoint has the required keys. A parser can only be merged if the datapoints for each function have the correct format.
    """
    standard_keys = ["datetime", "source"]
    keys_dict = {
        "production": ["zoneKey", "production"] + standard_keys,
        "consumption": ["zoneKey", "consumption"] + standard_keys,
        "exchange": ["sortedZoneKeys", "netFlow"] + standard_keys,
        "price": ["zoneKey", "currency", "price"] + standard_keys,
        "consumptionForecast": ["zoneKey", "value"] + standard_keys,
        "productionPerModeForecast": ["zoneKey", "production"] + standard_keys,
        "generationForecast": ["zoneKey", "value"] + standard_keys,
        "exchangeForecast": ["zoneKey", "netFlow"] + standard_keys,
    }
    for key in keys_dict[kind]:
        if key not in datapoint.keys():
            raise ValidationError(
                "{} - data point does not have the required keys:  {} is missing".format(
                    zone_key,
                    [key for key in keys_dict[kind] if key not in datapoint.keys()],
                ),
            )


def validate_reasonable_time(item, k):
    data_time = arrow.get(item["datetime"])
    if data_time.year < 2000:
        raise ValidationError(
            "Data from %s can't be before year 2000, it was " "%s" % (k, data_time)
        )

    arrow_now = arrow.utcnow()
    if data_time.astimezone(timezone.utc) > arrow_now:
        raise ValidationError(
            "Data from %s can't be in the future, data was %s, now is "
            "%s" % (k, data_time, arrow_now)
        )


def validate_consumption(obj: dict, zone_key: ZoneKey) -> None:
    validate_datapoint_format(datapoint=obj, kind="consumption", zone_key=zone_key)
    if (obj.get("consumption") or 0) < 0:
        raise ValidationError(
            "%s: consumption has negative value " "%s" % (zone_key, obj["consumption"])
        )
    # Plausibility Check, no more than 500GW
    if abs(obj.get("consumption") or 0) > 500000:
        raise ValidationError(
            "%s: consumption is not realistic (>500GW) "
            "%s" % (zone_key, obj["consumption"])
        )
    validate_reasonable_time(obj, zone_key)


def validate_exchange(item, k) -> None:
    validate_datapoint_format(datapoint=item, kind="exchange", zone_key=k)
    if item.get("sortedZoneKeys", None) != k:
        raise ValidationError(
            "Sorted country codes %s and %s don't "
            "match" % (item.get("sortedZoneKeys", None), k)
        )
    if "datetime" not in item:
        raise ValidationError("datetime was not returned for %s" % k)
    if type(item["datetime"]) != datetime:
        raise ValidationError(
            "datetime {} is not valid for {}".format(item["datetime"], k)
        )
    validate_reasonable_time(item, k)
    if "netFlow" not in item:
        raise ValidationError("netFlow was not returned for %s" % k)
    # Verify that the exchange flow is not greater than the interconnector
    # capacity and has physical sense (no exchange should exceed 100GW)
    # Use https://github.com/electricitymaps/electricitymaps-contrib/blob/master/parsers/example.py for expected format
    if item.get("sortedZoneKeys", None) and item.get("netFlow", None):
        zone_names: list[str] = item["sortedZoneKeys"]
        if abs(item.get("netFlow", 0)) > 100000:
            raise ValidationError(
                "netFlow %s exceeds physical plausibility (>100GW) for %s"
                % (item["netFlow"], k)
            )
        if len(zone_names) == 2:
            if (zone_names in EXCHANGES_CONFIG) and (
                "capacity" in EXCHANGES_CONFIG[zone_names]
            ):
                interconnector_capacities = EXCHANGES_CONFIG[zone_names]["capacity"]
                margin = 0.1
                if not (
                    min(interconnector_capacities) * (1 - margin)
                    <= item["netFlow"]
                    <= max(interconnector_capacities) * (1 + margin)
                ):
                    raise ValidationError(
                        "netFlow %s exceeds interconnector capacity for %s"
                        % (item["netFlow"], k)
                    )


def validate_production(obj: dict[str, Any], zone_key: ZoneKey) -> None:
    validate_datapoint_format(datapoint=obj, kind="production", zone_key=zone_key)
    if "datetime" not in obj:
        raise ValidationError("datetime was not returned for %s" % zone_key)
    if "countryCode" in obj:
        warn(
            "object has field `countryCode`. It should have "
            "`zoneKey` instead. In {}".format(obj)
        )
    if "zoneKey" not in obj and "countryCode" not in obj:
        raise ValidationError("zoneKey was not returned for %s" % zone_key)
    if not isinstance(obj["datetime"], datetime):
        raise ValidationError(
            "datetime {} is not valid for {}".format(obj["datetime"], zone_key)
        )
    if (obj.get("zoneKey", None) or obj.get("countryCode", None)) != zone_key:
        raise ValidationError(
            "Zone keys %s and %s don't match in %s"
            % (obj.get("zoneKey", None), zone_key, obj)
        )

    if (
        obj.get("production", {}).get("unknown", None) is None
        and obj.get("production", {}).get("coal", None) is None
        and obj.get("production", {}).get("oil", None) is None
        and obj.get("production", {}).get("gas", None) is None
        and zone_key
        not in [
            "CH",
            "NO",
            "AU-TAS",
            "DK-BHM",
            "US-CAR-YAD",
            "US-NW-SCL",
            "US-NW-CHPD",
            "US-NW-WWA",
            "US-NW-GCPD",
            "US-NW-TPWR",
            "US-NW-WAUW",
            "US-SE-SEPA",
            "US-NW-GWA",
            "US-NW-DOPD",
            "LU",
        ]
    ):
        raise ValidationError(
            "Coal, gas or oil or unknown production value is required for"
            " %s" % zone_key
        )

    if zone_key in ["US-CAR-YAD"]:
        if obj.get("production", {}).get("hydro", 0) < 5:
            raise ValidationError(
                "Hydro production value is required to be greater than 5 for %s"
                % zone_key
            )

    if obj.get("storage"):
        if not isinstance(obj["storage"], dict):
            raise ValidationError(
                "storage value must be a dict, was " "{}".format(obj["storage"])
            )
        not_allowed_keys = set(obj["storage"]) - {"battery", "hydro"}
        if not_allowed_keys:
            raise ValidationError(f"unexpected keys in storage: {not_allowed_keys}")
    for key, value in obj["production"].items():
        if value is None:
            continue
        if value < 0:
            raise ValidationError(f"{zone_key}: key {key} has negative value {value}")
        # Plausibility Check, no more than 500GW
        if value > 500000:
            raise ValidationError(
                "%s: production for %s is not realistic ("
                ">500GW) "
                "%s" % (zone_key, key, value)
            )

    for key in obj.get("production", {}).keys():
        if key not in emission_factors(zone_key).keys():
            raise ValidationError(
                "Couldn't find emission factor for '%s' in '%s'. Maybe you misspelled one of the production keys?"
                % (key, zone_key)
            )

    validate_reasonable_time(obj, zone_key)
