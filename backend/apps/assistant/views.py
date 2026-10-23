from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import UserRateThrottle
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.permissions import can_manage_property
from apps.tenancies.models import Tenancy

from .providers import ProviderUnavailable
from .serializers import RentQuestionSerializer
from .services import answer_rent_question


class RentQuestionThrottle(UserRateThrottle):
    scope = "rent_qa"


class RentQuestionView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [RentQuestionThrottle]

    def post(self, request, tenancy_id):
        tenancy = get_object_or_404(
            Tenancy.objects.select_related("tenant", "unit__property"),
            pk=tenancy_id,
        )
        if tenancy.tenant_id != request.user.pk and not can_manage_property(request.user, tenancy.unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = RentQuestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = answer_rent_question(question=serializer.validated_data["question"], tenancy=tenancy)
        except ProviderUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(result)

