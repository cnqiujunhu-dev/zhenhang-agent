"""Optional read-only API installation; preference writes use task confirmation."""
from fastapi import Depends

from .customer_benefits import benefits_view
from .customer_membership import membership_view
from .customer_preferences import preferences_view


def install_customer_profile_routes(application, factory, principal_dependency):
    @application.get("/api/v2/profile/family")
    def read_family_profile(principal=Depends(principal_dependency)):
        from .customer_profile_common import require_profile_actor
        from .family_profile_service import family_member_profiles
        from .occasion_service import occasion_view
        from .life_plan_service import life_plan_view
        from .customer_presentation import customer_business_value
        with factory() as session:
            require_profile_actor(session, principal)
            return customer_business_value({"members": family_member_profiles(session, principal),
                "occasions": occasion_view(session, principal), "plans": life_plan_view(session, principal)})

    @application.get("/api/v2/profile/membership")
    def read_membership(principal=Depends(principal_dependency)):
        with factory() as session:
            return membership_view(session, principal)

    @application.get("/api/v2/benefits")
    def read_benefits(benefit_name: str | None = None, principal=Depends(principal_dependency)):
        with factory() as session:
            return benefits_view(session, principal, benefit_name=benefit_name)

    @application.get("/api/v2/preferences")
    def read_preferences(preference_kind: str | None = None, principal=Depends(principal_dependency)):
        with factory() as session:
            return preferences_view(session, principal, preference_kind=preference_kind)
