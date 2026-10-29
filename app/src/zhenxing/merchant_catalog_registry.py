"""Registered catalogue names and category vocabulary; no intent routing."""

DEFAULT_CATALOG = (("FLOWER", "心意花店", "FLOWER", "BIRTHDAY_ROSES", "生日鲜花", 39900),
    ("CAKE", "生日蛋糕店", "CAKE", "BIRTHDAY_CAKE", "生日蛋糕", 59900),
    ("DINNER", "团圆餐厅", "RESTAURANT", "FAMILY_DINNER", "家庭生日套餐", 58800))

_CATEGORY_NAMES = {
    "FLOWER": ("鲜花", "flowers", "flower bouquet"),
    "CAKE": ("蛋糕", "cake", "birthday cake"),
    "RESTAURANT": ("家庭生日套餐", "餐厅套餐", "餐厅", "餐饮", "restaurant", "dinner", "restaurant meal"),
}

# A registered product's complete name is a category navigation name, not a
# substring classifier and not authority to buy that product. This prevents
# the same catalogue naming its cake while its field grammar rejects the name.
CATEGORY_ALIASES = {category: tuple(dict.fromkeys((*names, *(row[4] for row in DEFAULT_CATALOG
    if row[2] == category)))) for category, names in _CATEGORY_NAMES.items()}
