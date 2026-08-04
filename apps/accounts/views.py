from rest_framework.response import Response
from rest_framework.views import APIView


class MeView(APIView):
    """The authenticated user's identity and capabilities, so the frontend
    can role-gate actions (e.g. hide Dispose) without duplicating the
    role -> capability rules that already live on the User model."""

    def get(self, request):
        user = request.user
        return Response(
            {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "can_review_results": user.can_review_results,
                "can_approve_results": user.can_approve_results,
                "can_dispose_samples": user.can_dispose_samples,
                "can_adjust_inventory": user.can_adjust_inventory,
            }
        )
