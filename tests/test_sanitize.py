"""What the sanitizer must catch, and what it must leave alone.

Both halves matter. A sanitizer that redacts everything is safe and useless:
the digest exists so the agent can read it, and a digest of ***MASKED*** tells
it nothing. These cases are drawn from a real digest that carried two genuine
credentials and one table of statistics that merely talked about passwords.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "core" / "sync"))

from sanitize import MASK, mask_secrets, residual_secret_lines


class TestKnownShapes(unittest.TestCase):
    def test_recognisable_tokens_are_masked_anywhere(self):
        for secret in (
            "ghp_abcdefghijklmnopqrstuvwxyz0123",
            "xoxb-1234567890-abcdefghijkl",
            "glpat-abcdefghijklmnopqrst",
            "AKIAIOSFODNN7EXAMPLE",
            "sk-ant-abcdefghijklmnopqrstuvwxyz",
            "123456789:AAEhBOweik6ad9r_QXbFSPBHFPGCUuFHzwA",
        ):
            with self.subTest(secret=secret):
                out, n = mask_secrets(f"the value is {secret} ok")
                self.assertNotIn(secret, out)
                self.assertGreaterEqual(n, 1)

    def test_private_key_block_is_masked_whole(self):
        text = ("-----BEGIN OPENSSH PRIVATE KEY-----\n"
                "b3BlbnNzaC1rZXktdjEAAAAA\nmore\n"
                "-----END OPENSSH PRIVATE KEY-----")
        out, n = mask_secrets(text)
        self.assertEqual(out, MASK)
        self.assertEqual(n, 1)

    def test_key_value_pairs_keep_their_key(self):
        out, _ = mask_secrets('api_key: "sup3rs3cr3tvalue"')
        self.assertIn("api_key", out)
        self.assertNotIn("sup3rs3cr3tvalue", out)


class TestCuedValues(unittest.TestCase):
    """The shape a format-matching sanitizer cannot see: the giveaway is prose."""

    def test_chinese_cue_then_fenced_value(self):
        text = "**解密密碼（只顯示這一次）：**\n\n```\nQ7x2mK9pL4vN8wR3\n```\n"
        out, n = mask_secrets(text)
        self.assertNotIn("Q7x2mK9pL4vN8wR3", out)
        self.assertEqual(n, 1)

    def test_english_cue_then_next_line(self):
        text = "The passphrase is:\nT0p-S3cret_Value99\n"
        out, _ = mask_secrets(text)
        self.assertNotIn("T0p-S3cret_Value99", out)

    def test_cue_then_token_in_a_code_block(self):
        text = "貼上這組 Token：\n\n```\neyJhIjoiODg1NTM5ZTFjYzEwNDQyYTk2MDJmMzUz\n```\n"
        out, _ = mask_secrets(text)
        self.assertNotIn("eyJhIjoiODg1NTM5ZTFjYzEwNDQyYTk2MDJmMzUz", out)


class TestWhatMustSurvive(unittest.TestCase):
    """Over-redaction is a real cost, not a safe default."""

    def test_a_table_discussing_passwords_is_kept(self):
        # A cue word and a trailing colon, but the block is evidence.
        text = ("我們剛剛才把密碼認證關掉，而 fail2ban 的主戰場就是密碼暴力破解。"
                "近 14 天的實際數字：\n\n```\n"
                "Failed password    3   ← 這條路已經不存在了\n"
                "Invalid user       4\n"
                "Failed publickey   3\n"
                "```\n")
        out, n = mask_secrets(text)
        self.assertIn("Failed password    3", out)
        self.assertEqual(n, 0)

    def test_a_url_after_a_cue_is_not_a_secret(self):
        text = "管理後台的密碼你已改過，網址：\nhttps://example.com/_admin/\n"
        out, n = mask_secrets(text)
        self.assertIn("https://example.com/_admin/", out)
        self.assertEqual(n, 0)

    def test_a_file_path_after_a_cue_is_kept(self):
        text = "密碼存放位置：\n~/.credentials/infra-passwords.env\n"
        out, _ = mask_secrets(text)
        self.assertIn("~/.credentials/infra-passwords.env", out)

    def test_ordinary_prose_is_untouched(self):
        text = "路由器換成 BE7200 Pro，mesh 節點重新配對後 3F 恢復正常。\n"
        out, n = mask_secrets(text)
        self.assertEqual(out, text)
        self.assertEqual(n, 0)

    def test_masking_is_idempotent(self):
        text = "The passphrase is:\nT0p-S3cret_Value99\n"
        once, _ = mask_secrets(text)
        twice, n = mask_secrets(once)
        self.assertEqual(once, twice)
        self.assertEqual(n, 0)


class TestResidualDetection(unittest.TestCase):
    """The fail-closed half: what masking missed must be reportable."""

    def test_clean_text_has_no_residue(self):
        self.assertEqual(residual_secret_lines("nothing to see here\n"), [])

    def test_masked_output_has_no_residue(self):
        out, _ = mask_secrets("解密密碼：\n\n```\nQ7x2mK9pL4vN8wR3\n```\n")
        self.assertEqual(residual_secret_lines(out), [])

    def test_an_unmasked_cued_value_is_reported_by_line_not_content(self):
        # Hand-built: a cue and a value that masking did not reach.
        text = "password:\n\n```\nA1b2C3d4E5f6G7h8\n```\n"
        findings = residual_secret_lines(text)
        self.assertEqual(len(findings), 1)
        lineno, why = findings[0]
        self.assertEqual(lineno, 4)
        self.assertNotIn("A1b2C3d4E5f6G7h8", why)


if __name__ == "__main__":
    unittest.main()
