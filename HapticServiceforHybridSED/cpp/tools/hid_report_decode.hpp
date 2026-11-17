#pragma once
#include <windows.h>
#include <hidsdi.h>
#include <cstdint>
#include <span>
#include <sstream>
#include <vector>
#include <set>

namespace panel {
// Parse only descriptor-declared scalar values. Never guess vendor byte offsets.
inline std::int64_t signedHidValue(ULONG raw, USHORT bits, LONG minimum) {
    if (minimum >= 0 || bits == 0 || bits > 32) return raw;
    const auto modulus = std::uint64_t{1} << bits;
    const auto value = std::uint64_t{raw} & (modulus - 1);
    return (value & (modulus >> 1)) ? static_cast<std::int64_t>(value) - static_cast<std::int64_t>(modulus) : static_cast<std::int64_t>(value);
}
inline std::string decodeHidValues(PHIDP_PREPARSED_DATA parsed,
                                   std::span<const unsigned char> report) {
    if (!parsed || report.empty()) return "{\"available\":false}";
    HIDP_CAPS caps{};
    const auto status = HidP_GetCaps(parsed, &caps);
    if (status != HIDP_STATUS_SUCCESS) return "{\"available\":false,\"caps_status\":" + std::to_string(status) + "}";
    USHORT count = caps.NumberInputValueCaps;
    if (count > 1024) return "{\"available\":false,\"reason\":\"capability_limit\"}";
    std::vector<HIDP_VALUE_CAPS> values(count);
    const auto valuesStatus = count ? HidP_GetValueCaps(HidP_Input, values.data(), &count, parsed) : HIDP_STATUS_SUCCESS;
    std::ostringstream out;
    out << "{\"available\":true,\"value_caps_status\":" << valuesStatus << ",\"scalar_values\":[";
    bool first = true;
    unsigned skipped = 0, emitted = 0;
    if (valuesStatus == HIDP_STATUS_SUCCESS) for (USHORT i = 0; i < count; ++i) {
        const auto& v = values[i];
        if (v.ReportID && v.ReportID != report[0]) continue;
        if (v.ReportCount != 1) { ++skipped; continue; }
        const unsigned begin = v.IsRange ? v.Range.UsageMin : v.NotRange.Usage;
        const unsigned end = v.IsRange ? v.Range.UsageMax : v.NotRange.Usage;
        if (end < begin || end - begin > 64) { ++skipped; continue; }
        for (unsigned usage = begin; usage <= end && emitted < 256; ++usage) {
            ULONG raw = 0;
            const auto result = HidP_GetUsageValue(HidP_Input, v.UsagePage, v.LinkCollection,
                static_cast<USAGE>(usage), &raw, parsed,
                reinterpret_cast<PCHAR>(const_cast<unsigned char*>(report.data())), static_cast<ULONG>(report.size()));
            if (!first) out << ',';
            first = false; ++emitted;
            out << "{\"usage_page\":" << v.UsagePage << ",\"usage\":" << usage
                << ",\"link_collection\":" << v.LinkCollection << ",\"report_id\":" << unsigned(v.ReportID)
                << ",\"status\":" << result << ",\"logical_min\":" << v.LogicalMin
                << ",\"logical_max\":" << v.LogicalMax;
            if (result == HIDP_STATUS_SUCCESS)
                out << ",\"raw\":" << raw << ",\"value\":" << signedHidValue(raw, v.BitSize, v.LogicalMin);
            out << '}';
        }
    }
    out << "],\"button_groups\":[";
    USHORT buttonCount=caps.NumberInputButtonCaps;
    if(buttonCount<=1024){
        std::vector<HIDP_BUTTON_CAPS> buttons(buttonCount);
        if(buttonCount&&HidP_GetButtonCaps(HidP_Input,buttons.data(),&buttonCount,parsed)==HIDP_STATUS_SUCCESS){
            std::set<std::pair<USHORT,USHORT>> groups;
            bool firstGroup=true;
            for(USHORT i=0;i<buttonCount;++i){
                const auto& b=buttons[i];
                if(b.ReportID&&b.ReportID!=report[0])continue;
                if(!groups.emplace(b.UsagePage,b.LinkCollection).second)continue;
                ULONG length=HidP_MaxUsageListLength(HidP_Input,b.UsagePage,parsed);
                if(!length||length>4096)continue;
                std::vector<USAGE> usages(length);
                const auto result=HidP_GetUsages(HidP_Input,b.UsagePage,b.LinkCollection,usages.data(),&length,parsed,
                    reinterpret_cast<PCHAR>(const_cast<unsigned char*>(report.data())),static_cast<ULONG>(report.size()));
                if(!firstGroup)out<<',';firstGroup=false;
                out<<"{\"usage_page\":"<<b.UsagePage<<",\"link_collection\":"<<b.LinkCollection
                   <<",\"status\":"<<result<<",\"active_usages\":[";
                if(result==HIDP_STATUS_SUCCESS)for(ULONG u=0;u<length;++u){if(u)out<<',';out<<usages[u];}
                out<<"]}";
            }
        }
    }
    out << "],\"array_or_large_caps_skipped\":" << skipped
        << ",\"note\":\"Values are logical HID units, not screen pixels; contacts may span reports.\"}";
    return out.str();
}
}
