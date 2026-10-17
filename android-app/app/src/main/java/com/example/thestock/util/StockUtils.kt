package com.example.thestock.util

object StockUtils {
    // 국내 코스피/코스닥 및 주요 ETF 종목명 사전
    private val STOCK_NAME_MAP = mapOf(
        "005930" to "삼성전자",
        "000660" to "SK하이닉스",
        "005380" to "현대차",
        "000270" to "기아",
        "373220" to "LG에너지솔루션",
        "207940" to "삼성바이오로직스",
        "068270" to "셀트리온",
        "035420" to "NAVER",
        "035720" to "카카오",
        "005490" to "POSCO홀딩스",
        "051910" to "LG화학",
        "006400" to "삼성SDI",
        "012450" to "한화에어로스페이스",
        "042660" to "한화오션",
        "010140" to "삼성중공업",
        "267260" to "HD현대일렉트릭",
        "028300" to "HLB",
        "196170" to "알테오젠",
        "086520" to "에코프로",
        "247540" to "에코프로비엠",
        "069500" to "KODEX 200",
        "229200" to "KODEX 코스닥150",
        "122630" to "KODEX 레버리지",
        "233740" to "KODEX 코스닥150레버리지",
        "114800" to "KODEX 인버스",
        "252670" to "KODEX 200선물인버스2X",
        "005935" to "삼성전자우",
        "032830" to "삼성생명",
        "018260" to "삼성에스디에스",
        "003550" to "LG",
        "066570" to "LG전자",
        "017670" to "SK텔레콤",
        "030200" to "KT",
        "034730" to "SK",
        "032640" to "LG유플러스",
        "105560" to "KB금융",
        "055550" to "신한지주",
        "086790" to "하나금융지주",
        "316140" to "우리금융지주",
        "003670" to "포스코퓨처엠",
        "000810" to "삼성화재",
        "009540" to "HD한국조선해양",
        "001570" to "금양",
        "047050" to "포스코인터내셔널",
        "024110" to "기업은행",
        "011200" to "HMM"
    )

    /**
     * 종목코드에 해당하는 한국어 종목명을 반환합니다.
     */
    fun getStockName(ticker: String, fallbackName: String? = null): String {
        if (!fallbackName.isNullOrBlank()) return fallbackName
        return STOCK_NAME_MAP[ticker] ?: "종목 $ticker"
    }

    /**
     * "삼성전자 (005930)" 형식으로 포맷팅합니다.
     */
    fun formatDisplay(ticker: String, customName: String? = null): String {
        val name = getStockName(ticker, customName)
        return if (name.contains(ticker)) {
            name
        } else {
            "$name ($ticker)"
        }
    }

    /**
     * ISO 8601 문자열 또는 날짜 문자열을 "MM/dd HH:mm:ss" 형식으로 변환합니다.
     */
    fun formatDateTimeShort(dateTimeStr: String?): String {
        if (dateTimeStr.isNullOrBlank()) return "-"
        return try {
            val clean = dateTimeStr.replace("T", " ").substringBefore(".")
            if (clean.length >= 16) {
                // "2026-10-16 07:53:56" -> "10/16 07:53:56"
                val parts = clean.split(" ")
                val datePart = parts[0].substringAfter("-") // "10-16"
                val timePart = if (parts.size > 1) parts[1] else ""
                "${datePart.replace("-", "/")} $timePart"
            } else {
                clean
            }
        } catch (_: Exception) {
            dateTimeStr
        }
    }

    /**
     * "HH:mm:ss" 체결 시각만 추출합니다.
     */
    fun formatTimeOnly(dateTimeStr: String?): String {
        if (dateTimeStr.isNullOrBlank()) return "-"
        return try {
            val clean = dateTimeStr.replace("T", " ").substringBefore(".")
            if (clean.contains(" ")) {
                clean.substringAfter(" ")
            } else if (clean.contains(":")) {
                clean
            } else {
                clean
            }
        } catch (_: Exception) {
            dateTimeStr
        }
    }
}

