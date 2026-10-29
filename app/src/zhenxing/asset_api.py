from typing import Literal

from fastapi import Depends, Query

from .asset_positions import asset_positions_view


def install_asset_routes(app, get_session, get_principal):
    @app.get("/api/v2/assets/positions")
    def positions(asset_class: Literal["WEALTH", "STOCK", "PRECIOUS_METAL", "ALL"] = "ALL",
                  product_name: str | None = Query(default=None, min_length=1, max_length=100),
                  session=Depends(get_session), principal=Depends(get_principal)):
        return asset_positions_view(session, principal, asset_class=asset_class, product_name=product_name)
