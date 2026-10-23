from django.contrib.auth import authenticate
from rest_framework import serializers

from .models import User


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        request = self.context.get("request")
        user = authenticate(request=request, username=attrs["email"].lower(), password=attrs["password"])
        if user is None or not user.is_active:
            raise serializers.ValidationError("Invalid email or password.")
        attrs["user"] = user
        return attrs


class UserSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(source="profile.display_name", read_only=True)

    class Meta:
        model = User
        fields = ("id", "email", "first_name", "last_name", "display_name")
        read_only_fields = fields
