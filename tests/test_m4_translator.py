"""M4 翻译 — 纯逻辑单测（不访问网络，注入 fake client / 临时配置）。

运行：python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import unittest
from dataclasses import dataclass, field
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import httpx  # noqa: E402

import m4_translator as m4  # noqa: E402
from models import NewsDetail, TranslationResult  # noqa: E402

# 测试临时目录放在仓库内 .tmp/（已 gitignore）——本机沙箱只允许写工作区（系统 %TEMP% 被拒）。
# 与 M3 测试同法（.tmp/m3_tests），可移植。
_TMP_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tmp", "m4_tests")

SAMPLE_DETAIL = NewsDetail(
    id="01_17821",
    url="https://idolmaster-official.jp/news/01_17821",
    title="【イベント】アイドルマスター 新情報発表会 開催決定！",
    date="2026-08-26",
    body_text="第一段落のテキスト。\n\n第二段落のテキスト。",
    images=[],
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=None):
        self.status_code = status_code
        self._json = json_data
        self.text = text if text is not None else (
            json.dumps(json_data, ensure_ascii=False) if json_data is not None else ""
        )

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeClient:
    """记录调用并依次弹出预设响应；fail 若给出则在 post 时抛出。"""

    def __init__(self, responses=None, fail=None):
        self.responses = list(responses or [])
        self.fail = fail
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.fail is not None:
            raise self.fail
        if self.responses:
            r = self.responses.pop(0)
            if callable(r):
                return r(self)
            return r
        return FakeResponse(200, {"choices": [{"message": {"content": '{"title_zh":"标题","body_zh":"正文"}'}}]})


def ok_response(content: str) -> FakeResponse:
    return FakeResponse(200, {"choices": [{"message": {"content": content}}]})


class TestContractReExport(unittest.TestCase):
    def test_types_come_from_models(self):
        self.assertIs(m4.NewsDetail, NewsDetail)
        self.assertIs(m4.TranslationResult, TranslationResult)


class TestConfigLoading(unittest.TestCase):
    def setUp(self) -> None:
        shutil.rmtree(_TMP_BASE, ignore_errors=True)
        os.makedirs(_TMP_BASE, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(_TMP_BASE, ignore_errors=True)

    def _path(self, name: str) -> str:
        return os.path.join(_TMP_BASE, name)

    def _write(self, name: str, content: str) -> str:
        p = self._path(name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
        return p

    def _empty_cfg(self) -> m4.TranslatorConfig:
        return m4.load_config(
            config_path=self._path("nope.yaml"),
            env_path=self._path("nope.env"),
            terms_path=self._path("nope.json"),
        )

    def test_defaults_without_files(self):
        cfg = self._empty_cfg()
        self.assertEqual(cfg.api_key, "")
        self.assertEqual(cfg.base_url, "https://api.deepseek.com")
        self.assertEqual(cfg.model, "deepseek-chat")
        self.assertEqual(cfg.temperature, 0.3)
        self.assertEqual(cfg.max_retries, 2)
        self.assertEqual(cfg.terms, m4.DEFAULT_TERMS)

    def test_env_file_api_key(self):
        env = self._write(".env", "DEEPSEEK_API_KEY=sk-test-123\n")
        cfg = m4.load_config(config_path=self._path("nope.yaml"), env_path=env)
        self.assertEqual(cfg.api_key, "sk-test-123")

    def test_os_environ_wins_over_env_file(self):
        old = os.environ.get("DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_API_KEY"] = "sk-from-environ"
        try:
            env = self._write(".env", "DEEPSEEK_API_KEY=sk-from-file\n")
            cfg = m4.load_config(config_path=self._path("nope.yaml"), env_path=env)
            self.assertEqual(cfg.api_key, "sk-from-environ")
        finally:
            if old is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = old

    def test_config_yaml_overrides_and_terms_merge(self):
        self._write(
            "config.yaml",
            "# 测试配置\n"
            "translator:\n"
            "  base_url: https://api.deepseek.com/v1\n"
            '  model: "deepseek-chat"\n'
            "  temperature: 0.2\n"
            "  max_retries: 1\n"
            "terms:\n"
            "  アイドルマスター: 偶像大师改\n"
            "  新規ワード: 新词\n",
        )
        cfg = m4.load_config(config_path=self._path("config.yaml"), env_path=self._path("nope.env"))
        self.assertEqual(cfg.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(cfg.model, "deepseek-chat")
        self.assertEqual(cfg.temperature, 0.2)
        self.assertEqual(cfg.max_retries, 1)
        self.assertEqual(cfg.terms["アイドルマスター"], "偶像大师改")  # 覆盖内置
        self.assertEqual(cfg.terms["新規ワード"], "新词")  # 扩充
        self.assertEqual(cfg.terms["アイマス"], "爱马仕")  # 内置保留

    def test_config_json_fallback(self):
        self._write("config.json", json.dumps({"translator": {"model": "deepseek-reasoner"}}))
        cfg = m4.load_config(config_path=self._path("config.json"), env_path=self._path("nope.env"))
        self.assertEqual(cfg.model, "deepseek-reasoner")

    def test_terms_json_overrides(self):
        self._write(
            "terms.json",
            json.dumps({"アイマス": "IM@S", "新規ワード": "新词"}, ensure_ascii=False),
        )
        cfg = m4.load_config(
            config_path=self._path("nope.yaml"),
            env_path=self._path("nope.env"),
            terms_path=self._path("terms.json"),
        )
        self.assertEqual(cfg.terms["アイマス"], "IM@S")
        self.assertEqual(cfg.terms["新規ワード"], "新词")
        self.assertEqual(cfg.terms["アイドルマスター"], "偶像大师")  # 内置保留

    def test_dotenv_fallback_parser(self):
        old = m4.dotenv
        m4.dotenv = None
        try:
            env = self._write(".env", '# comment\nDEEPSEEK_API_KEY="sk-quoted"\n\nDEEPSEEK_MODEL=deepseek-chat\n')
            cfg = m4.load_config(config_path=self._path("nope.yaml"), env_path=env)
            self.assertEqual(cfg.api_key, "sk-quoted")
            self.assertEqual(cfg.model, "deepseek-chat")
        finally:
            m4.dotenv = old


class TestYamlSubset(unittest.TestCase):
    def test_basic_nesting_and_scalars(self):
        text = (
            "# 注释\n"
            "translator:\n"
            "  base_url: https://api.deepseek.com\n"
            '  model: "deepseek-chat"\n'
            "  temperature: 0.3  # 行尾注释\n"
            "  max_retries: 2\n"
            "flag: true\n"
            "empty:\n"
        )
        data = m4._parse_yaml_subset(text)
        self.assertEqual(data["translator"]["base_url"], "https://api.deepseek.com")
        self.assertEqual(data["translator"]["model"], "deepseek-chat")
        self.assertEqual(data["translator"]["temperature"], 0.3)
        self.assertEqual(data["translator"]["max_retries"], 2)
        self.assertIs(data["flag"], True)
        self.assertEqual(data["empty"], {})

    def test_terms_japanese_keys(self):
        text = "terms:\n  アイドルマスター: 偶像大师\n  SideM: SideM\n"
        data = m4._parse_yaml_subset(text)
        self.assertEqual(data["terms"]["アイドルマスター"], "偶像大师")
        self.assertEqual(data["terms"]["SideM"], "SideM")


class TestPromptBuilding(unittest.TestCase):
    def test_system_prompt_contains_terms_and_json_requirement(self):
        prompt = m4._build_system_prompt(m4.DEFAULT_TERMS)
        self.assertIn("アイドルマスター → 偶像大师", prompt)
        self.assertIn("只输出 JSON", prompt)
        self.assertIn('"title_zh"', prompt)

    def test_messages_structure(self):
        msgs = m4._build_messages(SAMPLE_DETAIL, m4.DEFAULT_TERMS)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["role"], "user")
        self.assertIn(SAMPLE_DETAIL.title, msgs[1]["content"])
        self.assertIn(SAMPLE_DETAIL.body_text, msgs[1]["content"])


class TestCoerceDetail(unittest.TestCase):
    def test_models_dataclass_passthrough(self):
        self.assertIs(m4._coerce_detail(SAMPLE_DETAIL), SAMPLE_DETAIL)

    def test_other_dataclass_with_contract_fields(self):
        """M2 并行期自建的 NewsDetail（同类字段）也能直接翻译。"""

        @dataclass
        class OtherDetail:
            id: str
            url: str
            title: str
            date: str
            body_text: str
            images: list = field(default_factory=list)

        d = OtherDetail("x", "u", "t", "d", "b", [])
        coerced = m4._coerce_detail(d)
        self.assertIsInstance(coerced, NewsDetail)
        self.assertEqual(coerced.title, "t")

    def test_dict_input(self):
        coerced = m4._coerce_detail(
            {"id": "x", "url": "u", "title": "t", "date": "d", "body_text": "b"}
        )
        self.assertEqual((coerced.id, coerced.url, coerced.title, coerced.date, coerced.body_text),
                         ("x", "u", "t", "d", "b"))

    def test_missing_field_raises(self):
        with self.assertRaises(m4.TranslationError):
            m4._coerce_detail({"id": "x", "title": "t"})

    def test_junk_type_raises(self):
        with self.assertRaises(m4.TranslationError):
            m4._coerce_detail(123)


class TestEndpoint(unittest.TestCase):
    def test_endpoint_normalization(self):
        self.assertEqual(m4._chat_endpoint("https://api.deepseek.com"), "https://api.deepseek.com/chat/completions")
        self.assertEqual(m4._chat_endpoint("https://api.deepseek.com/v1"), "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(m4._chat_endpoint("https://x.com/v1/chat/completions"), "https://x.com/v1/chat/completions")


class TestParse(unittest.TestCase):
    def test_parse_valid_json(self):
        r = m4._parse_translation('{"title_zh": "标题", "body_zh": "正文"}')
        assert r is not None
        self.assertEqual((r.title_zh, r.body_zh), ("标题", "正文"))

    def test_parse_code_fenced_json(self):
        r = m4._parse_translation('```json\n{"title_zh": "标题", "body_zh": "正文"}\n```')
        assert r is not None
        self.assertEqual(r.title_zh, "标题")

    def test_parse_non_json_returns_none(self):
        self.assertIsNone(m4._parse_translation("纯文本没有 JSON"))

    def test_parse_missing_field_returns_none(self):
        self.assertIsNone(m4._parse_translation('{"title_zh": "标题"}'))

    def test_strip_code_fence(self):
        self.assertEqual(m4._strip_code_fence("```\nhello\n```"), "hello")


def _cfg(api_key="sk-test") -> m4.TranslatorConfig:
    return m4.TranslatorConfig(api_key=api_key)


class TestTranslate(unittest.TestCase):
    def test_happy_path(self):
        client = FakeClient([ok_response('{"title_zh": "标题", "body_zh": "第一段\\n\\n第二段"}')])
        result = m4.translate(SAMPLE_DETAIL, config=_cfg(), client=client)
        self.assertIsInstance(result, TranslationResult)
        self.assertEqual(result.title_zh, "标题")
        self.assertEqual(result.body_zh, "第一段\n\n第二段")

    def test_payload_and_headers(self):
        client = FakeClient()
        m4.translate(SAMPLE_DETAIL, config=_cfg("sk-abc"), client=client)
        call = client.calls[0]
        self.assertTrue(call["url"].endswith("/chat/completions"))
        self.assertEqual(call["headers"]["Authorization"], "Bearer sk-abc")
        self.assertEqual(call["json"]["model"], "deepseek-chat")
        self.assertEqual(call["json"]["temperature"], 0.3)
        self.assertEqual(call["json"]["response_format"], {"type": "json_object"})
        self.assertEqual(len(call["json"]["messages"]), 2)

    def test_retry_transport_error_then_success(self):
        client = FakeClient(
            [lambda c: (_ for _ in ()).throw(httpx.ConnectError("boom")), ok_response('{"title_zh":"T","body_zh":"B"}')]
        )
        with mock.patch("m4_translator.time.sleep") as sleep:
            result = m4.translate(SAMPLE_DETAIL, config=_cfg(), client=client)
        self.assertEqual(result.title_zh, "T")
        self.assertEqual(len(client.calls), 2)
        sleep.assert_called_once()

    def test_retry_500_then_success(self):
        client = FakeClient([FakeResponse(500, {"error": {"message": "boom"}}), ok_response('{"title_zh":"T","body_zh":"B"}')])
        with mock.patch("m4_translator.time.sleep"):
            result = m4.translate(SAMPLE_DETAIL, config=_cfg(), client=client)
        self.assertEqual(result.title_zh, "T")
        self.assertEqual(len(client.calls), 2)

    def test_retries_exhausted_raises(self):
        client = FakeClient([FakeResponse(503, {"error": {"message": "busy"}})] * 3)
        with mock.patch("m4_translator.time.sleep"):
            with self.assertRaises(m4.TranslationError):
                m4.translate(SAMPLE_DETAIL, config=_cfg(), client=client)
        self.assertEqual(len(client.calls), 3)

    def test_401_fails_fast_without_retry(self):
        client = FakeClient([FakeResponse(401, {"error": {"message": "invalid key"}})])
        with self.assertRaises(m4.TranslationError):
            m4.translate(SAMPLE_DETAIL, config=_cfg(), client=client)
        self.assertEqual(len(client.calls), 1)

    def test_json_parse_fail_then_success_on_retry(self):
        client = FakeClient([ok_response("不是 JSON 的返回"), ok_response('{"title_zh":"T","body_zh":"B"}')])
        result = m4.translate(SAMPLE_DETAIL, config=_cfg(), client=client)
        self.assertEqual(result.title_zh, "T")
        self.assertEqual(len(client.calls), 2)

    def test_json_parse_fail_twice_falls_back_to_plain_text(self):
        client = FakeClient([ok_response("纯文本一"), ok_response("纯文本二")])
        result = m4.translate(SAMPLE_DETAIL, config=_cfg(), client=client)
        self.assertEqual(result.title_zh, SAMPLE_DETAIL.title)  # 回退：标题复制原文
        self.assertEqual(result.body_zh, "纯文本二")
        self.assertEqual(len(client.calls), 2)

    def test_missing_api_key_raises_before_network(self):
        client = FakeClient()
        with self.assertRaises(m4.TranslationError) as cm:
            m4.translate(SAMPLE_DETAIL, config=_cfg(api_key=""), client=client)
        self.assertIn("DEEPSEEK_API_KEY", str(cm.exception))
        self.assertEqual(client.calls, [])

    def test_dict_input_works(self):
        client = FakeClient()
        m4.translate(
            {"id": "x", "url": "u", "title": "t", "date": "d", "body_text": "b"},
            config=_cfg(),
            client=client,
        )
        self.assertEqual(len(client.calls), 1)
        self.assertIn("b", client.calls[0]["json"]["messages"][1]["content"])

    def test_invalid_input_raises(self):
        with self.assertRaises(m4.TranslationError):
            m4.translate(123, config=_cfg(), client=FakeClient())


if __name__ == "__main__":
    unittest.main()
