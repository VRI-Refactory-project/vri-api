import phonenumbers
from django.conf import settings
from rest_framework import serializers

NETWORKS = ("MTN", "AIRTEL")


def phone_digits(value):
    """A Ugandan or international number as digits only (2567…), or a ValidationError."""
    try:
        number = phonenumbers.parse(str(value), "UG")
    except phonenumbers.NumberParseException:
        raise serializers.ValidationError("Enter a valid phone number.") from None
    if not phonenumbers.is_valid_number(number):
        raise serializers.ValidationError("Enter a valid phone number.")
    return phonenumbers.format_number(
        number, phonenumbers.PhoneNumberFormat.E164
    ).lstrip("+")


class MoneyRequestSerializer(serializers.Serializer):
    """The amount, the mobile money network, and the number (the person's own if left out)."""

    amount = serializers.IntegerField(help_text="Whole UGX.")
    network = serializers.ChoiceField(choices=NETWORKS)
    phone_number = serializers.CharField(required=False)

    max_amount = None  # a subclass sets a ceiling

    def validate_amount(self, value):
        minimum = settings.WALLET_MIN_AMOUNT
        if value < minimum:
            raise serializers.ValidationError(
                f"The smallest amount is UGX {minimum:,}."
            )
        if self.max_amount and value > self.max_amount:
            raise serializers.ValidationError(
                f"The largest amount is UGX {self.max_amount:,}."
            )
        return value

    def validate_phone_number(self, value):
        return phone_digits(value)

    def validate(self, attrs):
        if "phone_number" not in attrs:
            attrs["phone_number"] = phone_digits(
                self.context["request"].user.phone_number
            )
        return attrs


class TopUpRequestSerializer(MoneyRequestSerializer):
    @property
    def max_amount(self):
        return settings.WALLET_MAX_TOPUP


class PayoutRequestSerializer(MoneyRequestSerializer):
    pass


class EntrySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()
    kind = serializers.CharField()
    status = serializers.CharField()
    amount = serializers.IntegerField(help_text="Whole UGX; negative means money out.")
    currency = serializers.CharField()
    description = serializers.CharField()
    failure_reason = serializers.CharField()
    network = serializers.CharField()
    phone_ending = serializers.CharField(help_text="Last four digits only.")
    created_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)


def entry_data(entry):
    return {
        "id": entry.id,
        "reference": entry.reference,
        "kind": entry.kind,
        "status": entry.status,
        "amount": entry.amount,
        "currency": entry.currency,
        "description": entry.description,
        "failure_reason": entry.failure_reason,
        "network": entry.network,
        "phone_ending": entry.phone_number[-4:],
        "created_at": entry.created_at,
        "completed_at": entry.completed_at,
    }


class WalletSerializer(serializers.Serializer):
    currency = serializers.CharField()
    payments_available = serializers.BooleanField(
        help_text="False while no payment provider keys are set."
    )
    available = serializers.IntegerField()
    pending = serializers.IntegerField()
    earned = serializers.IntegerField(
        help_text="Everything that has come in and settled, whether or not it was paid out since."
    )
    transactions = EntrySerializer(many=True)
