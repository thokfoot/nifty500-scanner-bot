"""
US top liquid stocks - S&P 500 core + Nasdaq-100 overlap (~470 tickers).
Some tickers may fail on yfinance (delisted/renamed) and are skipped gracefully.
"""

US_TICKERS = [
    # -- Mega cap tech --
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AVGO",
    "ORCL", "CRM", "ADBE", "AMD", "INTC", "MU", "QCOM", "TXN", "AMAT", "LRCX",
    "KLAC", "ASML", "ADI", "MRVL", "NXPI", "ON", "MPWR", "SWKS", "QRVO", "TER",
    "ENTG", "MCHP", "SMCI", "DELL", "HPQ", "WDC", "STX", "ANET", "CSCO",
    "ACN", "IBM", "NOW", "INTU", "PANW", "CRWD", "SNOW", "PLTR", "DDOG",
    "ZS", "NET", "MDB", "FTNT", "TEAM", "WDAY", "HUBS", "VEEV", "ZM", "OKTA",
    "APP", "AXON", "CDNS", "SNPS", "ADSK", "ANSS", "PTC", "TYL", "PCTY",
    "IT", "CTSH", "INFY", "EPAM", "GEN", "CDW", "FFIV", "JNPR", "AKAM",
    "EA", "TTWO", "RBLX", "U", "NFLX", "DIS", "WBD", "PARA", "SPOT", "TME",
    # -- Consumer / Retail --
    "WMT", "COST", "TGT", "HD", "LOW", "KR", "ACI", "DG", "DLTR", "BJ",
    "ROST", "TJX", "NKE", "LULU", "SBUX", "CMG", "MCD", "YUM", "QSR", "DPZ",
    "DRI", "EAT", "CAKE", "TXRH", "WING", "CAVA", "SG", "EL", "CL", "KMB",
    "PG", "COL", "KHC", "K", "GIS", "CPB", "HSY", "MKC", "STZ", "TAP", "KO",
    "PEP", "MNST", "CELH", "KVUE", "CHD", "COTY", "ULTA", "SEPH", "CVS", "WBA",
    "TSCO", "FIVE", "OLLI", "URLSV", "DASH", "ABNB", "BKNG", "EXPE", "MAR",
    "HLT", "IHG", "RCL", "CCL", "NCLH", "LVS", "WYNN", "MGM", "CZR", "DKNG",
    "VFC", "RL", "COH", "TPR", "BIRK", "ONON", "DECK", "CROX", "SKX", "WWW",
    # -- Financials --
    "JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW", "BLK", "BX", "KKR",
    "APO", "ARES", "TROW", "BEN", "IVZ", "STT", "BK", "NTRS", "MTB", "FITB",
    "HBAN", "RF", "CFG", "KEY", "ZION", "CMA", "WAL", "EWBC", "PB", "USB",
    "PNC", "TD", "RY", "BMO", "BNS", "ALLY", "SYF", "DFS", "COF",
    "AXP", "V", "MA", "PYPL", "SQ", "FI", "GPN", "FOUR", "AFRM",
    "UPST", "LC", "MET", "PRU", "AIG", "AFL", "ALL", "TRV", "CB", "CINF",
    "PGR", "HIG", "UNM", "GL", "SLF", "LMND", "ROOT",
    "COIN", "HOOD", "SOFI", "NU", "MELI", "PAGS", "PSFE",
    # -- Healthcare / Pharma --
    "UNH", "LLY", "JNJ", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "GILD", "BIIB", "REGN", "VRTX", "MRNA", "BNTX", "NBIX", "EXEL",
    "INCY", "ALNY", "SRPT", "BMRN", "RARE", "UTHR", "MEDP", "CRL",
    "IQV", "DOCS", "HIMS", "OSCR", "CLOV", "GDRX", "TDOC", "ISRG", "SYK",
    "BSX", "EW", "DXCM", "PODD", "HOLX", "RMD", "XRAY", "ALGN",
    "CI", "ELV", "HUM", "CNC", "MOH", "UHS", "THC", "CYH", "EHC", "ENH",
    # -- Energy --
    "XOM", "CVX", "COP", "EOG", "SLB", "PSX", "MPC", "VLO", "OXY", "HES",
    "DVN", "FANG", "APA", "MRO", "CTRA", "HAL", "BKR", "WMB", "KMI", "OKE",
    "ET", "EPD", "LNG", "TRGP", "DINO", "PBF", "DK", "MTDR", "CHRD", "CIVI",
    "AR", "RRC", "EQ", "EXE", "CNX", "SBOW", "GPRK", "TPL", "VTLE", "STR",
    # -- Industrials --
    "CAT", "DE", "CNI", "UNP", "CSX", "NSC", "DAL", "UAL", "AAL", "LUV",
    "ALK", "JBLU", "GE", "GEV", "HON", "MMM", "EMR", "ETN", "ROK",
    "PH", "DOV", "XYL", "FTV", "IR", "CMI", "PCAR", "TEX", "OSK", "MTW",
    "URI", "RRX", "GNRC", "PWR", "EME", "FIX", "MYRG", "PRIM", "ROAD", "AGX",
    "BA", "LMT", "RTX", "NOC", "GD", "LHX", "HWM", "TDG", "HEI", "CW",
    "TXT", "ERJ", "SPR", "KTOS", "AVAV", "PL", "RKLB", "AST", "LUNR",
    # -- Materials / Mining --
    "LIN", "APD", "SHW", "ECL", "DD", "DOW", "LYB", "CE", "PPG", "ALB",
    "FCX", "NEM", "GOLD", "AEM", "KGC", "CDE", "SSRM", "HMY", "BTG", "WPM",
    "RGLD", "FNV", "CCJ", "UEC", "URE", "MOS", "CF", "NTR", "IP", "PKG",
    "WRK", "SEE", "AVY", "BALL", "CCK", "SLGN", "STM", "SUM", "MLM", "VMC",
    # -- Autos / Transport --
    "F", "GM", "RIVN", "LCID", "NIO", "XPEV", "LI", "STLA", "TM",
    "HMC", "RACE", "EVGO", "BLNK",
    "ODFL", "XPO", "SAIA", "TFII", "KNX", "WERN", "SNDR", "HTLD", "MRTN",
    "UPS", "FDX", "GLNG", "EXPD", "CHRW", "HUBG", "RXO",
    # -- REITs / Utilities / Staples --
    "PLD", "AMT", "CCI", "SBAC", "DLR", "EQIX", "VICI", "O", "SPG", "WELL",
    "AVB", "EQR", "MAA", "ESS", "UDR", "INVH", "AMH", "ELS", "SUI", "REXR",
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "XEL", "ED", "PEG", "WEC",
    "ES", "AWK", "AEE", "DTE", "PPL", "FE", "CMS", "CNP", "ATO", "NI",
    # -- Media / Comm / Other growth --
    "T", "VZ", "TMUS", "CHTR", "CABO", "WOW", "LUMN", "FYBR", "TKO", "WMG",
    "LYV", "MSGS", "EVRG", "NRDS", "RDDT", "INST", "AFRM", "TWLO", "MTCH",
    "PINS", "SNAP", "EBAY", "ETSY", "W", "CHWY", "GME", "FVRR",
    "UPWK", "ZIP", "CPNG", "SE", "GRAB", "TOST", "LSPD", "SHOP",
]


def get_us_tickers():
    """Deduped, sorted list; yfinance symbol = ticker (no suffix)."""
    return sorted(set(t for t in US_TICKERS if t.isalpha()))
