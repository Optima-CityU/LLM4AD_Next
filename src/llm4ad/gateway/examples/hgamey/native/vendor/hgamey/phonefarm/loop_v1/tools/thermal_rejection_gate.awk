# Embedded into the generated thermal_sample.sh, never installed as a new tool.
function diagnostic_line(line) {
    if (diagnostic_bytes + length(line) + 1 > 65536) {
        diagnostic_truncated = 1
        return
    }
    diagnostic_bytes += length(line) + 1
    print line
}
function escaped_prefix(raw, out, j, c) {
    prefix_replaced = 0
    out = ""
    for (j = 1; j <= length(raw); j++) {
        c = substr(raw, j, 1)
        if (c == "\\") out = out "\\\\"
        else if (c == "\t") out = out "\\t"
        else if (c == "\r") out = out "\\r"
        else if (c ~ /^[ -~]$/) out = out c
        else { out = out "?"; prefix_replaced = 1 }
    }
    return out
}
BEGIN {
    __FROZEN_SENSOR_SETUP__
    expected_count = __EXPECTED_COUNT__
    bounds_source = "__BOUNDS_SOURCE__"
}
{
    # An extra row previously faulted on scale[NR]==0, also yielding exit82.
    # Count rejection is unchanged; avoid that fault so the count is recorded.
    if (NR > expected_count) { bad = 1; next }
    v = $0 / scale[NR]
    invalid = ($0 !~ /^-?[0-9]+([.][0-9]+)?$/)
    if (invalid || v < low[NR] || v > high[NR]) bad = 1
    raw_prefix[NR] = substr($0, 1, 64)
    raw_chars[NR] = length($0)
    normalized[NR] = v
    key[NR] = invalid ? "raw_format_invalid" : v < low[NR] ? "value_below_low" : v > high[NR] ? "value_above_high" : "none"
}
END {
    rejected = (bad || NR != expected_count)
    if (rejected) {
        diagnostic_line("#phonefarm_thermal_rejection v1")
        diagnostic_line(sprintf("COUNTS\t%d\t%d", expected_count, NR))
        diagnostic_line("MAPPING\t" (NR == expected_count ? "fixed_order_expected_paths" : "ordinal_only_count_mismatch"))
        diagnostic_line("FIELDS\tindex\texpected_path\tunit\tscale\teffective_low_c\teffective_high_c\toperating_cap_c\tbounds_source\traw_prefix\traw_chars\traw_truncated\tprefix_replaced\tnormalized_c\tfailed_key")
        if (NR != expected_count) diagnostic_line("FAIL\tvalue_count_mismatch")
        # Keep the failed key visible before the full sensor table in callers
        # that display only a bounded prefix of command.stderr.
        for (i = 1; i <= expected_count; i++) {
            if (i > NR) diagnostic_line(sprintf("FAIL\tmissing_record\t%d\t%s", i, expected_path[i]))
            else if (key[i] != "none") diagnostic_line(sprintf("FAIL\t%s\t%d\t%s", key[i], i, expected_path[i]))
        }
        for (i = 1; i <= expected_count; i++) {
            present = (i <= NR)
            prefix = present ? escaped_prefix(raw_prefix[i]) : "MISSING"
            replaced = present ? prefix_replaced : 0
            value_c = present && key[i] != "raw_format_invalid" ? sprintf("%.17g", normalized[i]) : "UNKNOWN"
            failed_key = present ? key[i] : "missing_record"
            diagnostic_line(sprintf("SENSOR\t%d\t%s\t%s\t%.17g\t%.17g\t%.17g\t%.17g\t%s\t%s\t%d\t%d\t%d\t%s\t%s", i, expected_path[i], declared_unit[i], scale[i], low[i], high[i], operating_cap[i], bounds_source, prefix, present ? raw_chars[i] : 0, present && raw_chars[i] > 64 ? 1 : 0, replaced, value_c, failed_key))
        }
        if (!diagnostic_truncated) diagnostic_line("#end_thermal_rejection")
    }
    exit rejected
}
