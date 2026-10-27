from django.test import SimpleTestCase

from .emailing import branded_html


class BrandedHtmlTests(SimpleTestCase):
    def test_wraps_the_body_in_the_standard_layout(self):
        html = branded_html("Hello Amina,\n\nYour account is approved.\n")
        self.assertIn("SignConnect", html)
        self.assertIn("Hello Amina,", html)
        self.assertIn("Your account is approved.", html)
        self.assertIn("The SignConnect team", html)

    def test_code_in_a_sentence_goes_in_a_box(self):
        html = branded_html("Hello,\n\nYour code is: 123456\n\nIt expires soon.", highlight="123456")
        self.assertIn(">123456</td>", html)
        self.assertIn("Your code is:", html)
        self.assertIn("It expires soon.", html)

    def test_code_on_its_own_line_goes_in_a_box(self):
        html = branded_html("Sign in with:\n\n    Temp-Pass-9\n\nThen change it.", highlight="Temp-Pass-9")
        self.assertIn(">Temp-Pass-9</td>", html)

    def test_text_is_escaped(self):
        html = branded_html("Reason: <script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


class CodeEmailTests(SimpleTestCase):
    def _html(self, **overrides):
        from .emailing import code_email_html

        args = dict(
            heading="Verify your email address",
            greeting="Hello Amina,",
            intro="Enter this code to confirm this email address is yours.",
            code="482910",
            expires_text="This code expires in 15 minutes and works once.",
            safety_note="Did not create an account? Ignore this email.",
        )
        args.update(overrides)
        return code_email_html(**args)

    def test_shows_heading_code_expiry_and_safety_note(self):
        html = self._html()
        self.assertIn("Verify your email address", html)
        self.assertIn(">482910</div>", html)
        self.assertIn("expires in 15 minutes", html)
        self.assertIn("Ignore this email", html)

    def test_preheader_defaults_to_the_code(self):
        self.assertIn("Your SignConnect code is 482910", self._html())

    def test_text_is_escaped(self):
        html = self._html(greeting="Hello <b>Amina</b>,")
        self.assertNotIn("<b>Amina</b>", html)
        self.assertIn("&lt;b&gt;Amina&lt;/b&gt;", html)
