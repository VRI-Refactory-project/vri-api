from django.core.exceptions import ValidationError


class NumberAndSpecialCharacterValidator:
    """Passwords must contain at least one number and at least one special character (Sprint 1).

    A special character is anything that is not a letter, a digit or a space, for example ! @ # $ % - _
    The length rule lives next to this one in AUTH_PASSWORD_VALIDATORS (MinimumLengthValidator).
    """

    def validate(self, password, user=None):
        errors = []
        if not any(ch.isdigit() for ch in password):
            errors.append(ValidationError("This password must contain at least one number.", code="no_number"))
        if not any(not (ch.isalnum() or ch.isspace()) for ch in password):
            errors.append(
                ValidationError(
                    "This password must contain at least one special character, for example ! @ # $ %.",
                    code="no_special_character",
                )
            )
        if errors:
            raise ValidationError(errors)

    def get_help_text(self):
        return "Your password must contain at least one number and one special character."
