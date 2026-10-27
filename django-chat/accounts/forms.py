from django import forms


class VerifyCodeForm(forms.Form):
    code = forms.CharField(
        label="Verification code",
        max_length=12,
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "placeholder": "000000",
                "autofocus": True,
            }
        ),
    )
