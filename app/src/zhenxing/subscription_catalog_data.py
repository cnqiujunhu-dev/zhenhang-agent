"""Public registered subscription seed vocabulary; no I/O or execution.

Names can ground a resource noun, never prove current availability, ownership,
price, entitlement, renewal status or payment permission. Read current terms
and owned records through the normal domain before presenting those facts.
"""
CATALOG_DEFAULTS = (("视频会员", 2500, "BANK_MANDATE"), ("云存储", 1800, "MERCHANT_SIM"),
                    ("音乐会员", 1500, "MERCHANT_SIM"))
PUBLIC_PRODUCT_NAMES = {"视频会员": ("video", "video membership"), "云存储": ("cloud", "cloud storage"),
                        "音乐会员": ("music", "music membership")}
TERMS_VERSION = "subscription-commerce-v1"
MERCHANT_ALIASES = {"video membership": "视频会员", "video subscription": "视频会员",
    "cloud storage": "云存储", "cloud storage subscription": "云存储",
    "music membership": "音乐会员", "music subscription": "音乐会员"}
