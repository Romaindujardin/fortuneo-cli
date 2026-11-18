from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pytest
import requests
from woob.browser.exceptions import ClientError
from woob.exceptions import (
    ActionNeeded,
    BrowserIncorrectPassword,
    BrowserQuestion,
    NeedInteractiveFor2FA,
)
from woob.tools.value import ValueTransient

from fortuneo_sidecar.credentials import Credentials
from fortuneo_sidecar.engine import FortuneoEngine
from fortuneo_sidecar.otp import OTPProvider
from fortuneo_sidecar.paths import SidecarPaths
from fortuneo_sidecar.vendor.woob_modules.fortuneo.browser import FortuneoBrowser

from .conftest import FakeBackend, make_account

SCA_PAYLOAD = {
    "error": "Strong customer authentication is required.",
    "error_code": "SCA_REQUESTED",
    "operationId": "operation-1",
    "sca_tmp_token": "tmp-token",
    "sca_medias": [
        {
            "priority": 0,
            "type_media": "SMS_MFA2",
            "numero_masque": "06 XX XX XX XX",
            "numero_crypte": "crypted",
        }
    ],
}


def make_browser(*, interactive: bool = True) -> FortuneoBrowser:
    """Browser without network: skip __init__, which fetches auth parameters."""
    browser = object.__new__(FortuneoBrowser)
    browser.is_interactive = interactive
    browser.config = {"sca_code": ValueTransient("sca_code", masked=True)}
    browser.client_id = "client-id"
    browser.username = "login"
    browser.password = "password"
    browser.authorization_code_uri = "https://api.invalid/oauth-pkce/authorization-code"
    browser.session_id = "session-1"
    browser.sca_operation_id = None
    browser.sca_auth_method = None
    browser.session = requests.Session()
    browser.logger = MagicMock()
    browser.location = MagicMock()
    browser.response = MagicMock()
    browser.response.status_code = 200
    browser.response.headers = {}
    browser.build_authorization_uri = MagicMock(return_value="https://api.invalid/authorize")
    browser.url = "https://mabanque.fortuneo.fr/mon-espace#session_id=session-1"
    browser.request_access_token = MagicMock()
    browser.checkaccess = MagicMock()
    return browser


def client_error(status_code: int, payload: dict | None = None) -> ClientError:
    response = requests.Response()
    response.status_code = status_code
    if payload is not None:
        response._content = json.dumps(payload).encode()
        response.headers["Content-Type"] = "application/json"
    return ClientError(f"{status_code}", response=response)


def test_sca_interactive_run_sends_the_sms_then_asks_for_the_code():
    browser = make_browser()

    with pytest.raises(BrowserQuestion) as raised:
        browser.handle_sca(SCA_PAYLOAD)

    browser.location.assert_called_once()
    assert browser.location.call_args.args[0] == "https://api.fortuneo.fr/security/operation/otp"
    assert browser.location.call_args.kwargs["json"]["operationId"] == "operation-1"
    assert browser.location.call_args.kwargs["json"]["valueMedia"] == "crypted"
    assert browser.location.call_args.kwargs["json"]["typeMedia"] == "SMS_MFA2"
    assert browser.location.call_args.kwargs["headers"]["apikey"] == "client-id"
    assert browser.location.call_args.kwargs["headers"]["Authorization"] == "Bearer tmp-token"

    (field,) = raised.value.fields
    assert field.id == "sca_code"
    assert field.masked is True
    assert "06 XX XX XX XX" in field.label


def test_sca_unattended_run_does_not_send_an_sms():
    browser = make_browser(interactive=False)

    with pytest.raises(NeedInteractiveFor2FA):
        browser.handle_sca(SCA_PAYLOAD)

    browser.location.assert_not_called()


def test_sca_without_media_is_an_action_needed():
    browser = make_browser()
    payload = dict(SCA_PAYLOAD, sca_medias=[])

    with pytest.raises(ActionNeeded):
        browser.handle_sca(payload)

    browser.location.assert_not_called()


def test_initial_authorization_detects_sca_403():
    browser = make_browser()
    browser._submit_authorization_code = MagicMock(side_effect=client_error(403, SCA_PAYLOAD))
    browser.handle_sca = MagicMock()

    browser.request_authorization()

    browser.handle_sca.assert_called_once_with(SCA_PAYLOAD)


def test_initial_authorization_accepts_sca_required_403():
    browser = make_browser()
    payload = dict(SCA_PAYLOAD, error_code="SCA_REQUIRED")
    browser._submit_authorization_code = MagicMock(side_effect=client_error(403, payload))
    browser.handle_sca = MagicMock()

    browser.request_authorization()

    browser.handle_sca.assert_called_once_with(payload)


def test_initial_authorization_does_not_swallow_other_403():
    browser = make_browser()
    error = client_error(403, {"error_code": "FINGERPRINT_NOT_FOUND"})
    browser._submit_authorization_code = MagicMock(side_effect=error)

    with pytest.raises(ClientError) as raised:
        browser.request_authorization()

    assert raised.value is error


def test_sca_code_is_cleared_once_used():
    browser = make_browser()
    browser.config["sca_code"].set("123456")
    browser._submit_authorization_code = MagicMock()
    browser._finish_authorization = MagicMock()

    browser.request_authorization()

    assert browser.config["sca_code"].get() is None
    browser._submit_authorization_code.assert_called_once_with(
        password="123456",
        authenticationMethod=None,
        operationId=None,
    )
    browser._finish_authorization.assert_called_once_with()


def test_refused_sca_code_raises_a_clear_error_and_is_still_cleared():
    browser = make_browser()
    browser.config["sca_code"].set("123456")
    browser.sca_operation_id = "operation-1"
    browser.sca_auth_method = "SMS_MFA2"
    browser._submit_authorization_code = MagicMock(side_effect=client_error(403))
    browser._finish_authorization = MagicMock()

    with pytest.raises(BrowserIncorrectPassword, match="SMS code"):
        browser.request_authorization()

    assert browser.config["sca_code"].get() is None
    browser._finish_authorization.assert_not_called()


@pytest.mark.parametrize(
    "location",
    [
        "https://mabanque.fortuneo.fr/mon-espace#code=abc123&session_id=s",
        "https://mabanque.fortuneo.fr/mon-espace?code=abc123#session_id=s",
    ],
)
def test_callback_code_is_read_from_fragment_or_query(location):
    browser = make_browser()
    browser.response.headers = {"Location": location}

    browser._finish_authorization()

    assert browser.code == "abc123"
    browser.request_access_token.assert_called_once_with({"code": "abc123"})
    browser.checkaccess.go.assert_called_once_with(data={})


def test_callback_error_is_mapped_without_leaking_values():
    browser = make_browser()
    browser.response.headers = {
        "Location": "https://mabanque.fortuneo.fr/mon-espace#error=access_denied&secret=do-not-leak"
    }

    with pytest.raises(BrowserIncorrectPassword, match="access_denied") as raised:
        browser._finish_authorization()

    assert "do-not-leak" not in str(raised.value)


def test_callback_access_denied_includes_safe_description():
    browser = make_browser()
    browser.response.headers = {
        "Location": "https://mabanque.fortuneo.fr/mon-espace"
        "#error=access_denied&error_description=authentication-failed"
    }

    with pytest.raises(BrowserIncorrectPassword, match="authentication-failed"):
        browser._finish_authorization()


def test_callback_missing_location_is_diagnostic():
    browser = make_browser()
    browser.response.status_code = 200
    browser.response.headers = {}

    with pytest.raises(Exception, match="ni code ni URL") as raised:
        browser._finish_authorization()

    assert "200" in str(raised.value)


def test_callback_missing_code_and_error_is_diagnostic():
    browser = make_browser()
    browser.response.status_code = 302
    browser.response.headers = {
        "Location": "https://mabanque.fortuneo.fr/mon-espace#session_id=do-not-leak"
    }

    with pytest.raises(Exception, match="paramètres") as raised:
        browser._finish_authorization()

    message = str(raised.value)
    assert "session_id" in message
    assert "do-not-leak" not in message
    assert "302" in message


def test_engine_reports_oauth_refusal_detail(tmp_path):
    class RefusedBackend(FakeBackend):
        def iter_accounts(self):
            raise BrowserIncorrectPassword(
                "Fortuneo a refusé l'authentification (access_denied/authentication-failed)."
            )

    engine = FortuneoEngine(
        Credentials("login", "password"),
        SidecarPaths.from_root(tmp_path).ensure(),
        otp_provider=OTPProvider(env={}, interactive=False),
        backend_factory=lambda: RefusedBackend([]),
    )

    with pytest.raises(Exception, match="access_denied/authentication-failed"):
        engine.accounts()


def test_sca_resume_reuses_the_original_session_and_media():
    browser = make_browser()
    browser._submit_authorization_code = MagicMock(
        side_effect=[client_error(403, SCA_PAYLOAD), None]
    )
    browser._finish_authorization = MagicMock()

    with pytest.raises(BrowserQuestion) as raised:
        browser.request_authorization()

    (field,) = raised.value.fields
    assert field.id == "sca_code"

    browser.config["sca_code"].set("123456")
    browser.request_authorization()

    # Second call resumes the challenge instead of starting a new OAuth flow.
    browser.build_authorization_uri.assert_called_once_with()
    resume = browser._submit_authorization_code.call_args_list[1]
    assert resume.kwargs == {
        "password": "123456",
        "authenticationMethod": "SMS_MFA2",
        "operationId": "operation-1",
    }
    assert browser.session_id == "session-1"
    browser._finish_authorization.assert_called_once_with()


def test_engine_answers_sca_code_question_and_clears_it(tmp_path):
    class ScaBackend(FakeBackend):
        def __init__(self, accounts, expected_code):
            super().__init__(accounts)
            self.expected_code = expected_code
            self.config["sca_code"] = type(self.config["code"])(None)
            self.sca_questions = 0

        def iter_accounts(self):
            code = self.config["sca_code"].get()
            if code is None:
                self.sca_questions += 1
                raise BrowserQuestion(ValueTransient("sca_code", masked=True))
            if code != self.expected_code:
                raise BrowserIncorrectPassword("bad sca code")
            return list(self.accounts)

    backend = ScaBackend([make_account("acc-sca")], "123456")
    engine = FortuneoEngine(
        Credentials("login", "password"),
        SidecarPaths.from_root(tmp_path).ensure(),
        otp_provider=OTPProvider(env={"FORTUNEO_OTP": "123456"}, interactive=False),
        backend_factory=lambda: backend,
    )

    assert [account.id for account in engine.accounts()] == ["acc-sca"]
    assert backend.sca_questions == 1
    assert backend.config["sca_code"].get() is None
    engine.close()


def test_engine_sca_question_without_code_channel_requires_otp(tmp_path):
    class ScaBackend(FakeBackend):
        def __init__(self, accounts):
            super().__init__(accounts)
            self.config["sca_code"] = type(self.config["code"])(None)

        def iter_accounts(self):
            if self.config["sca_code"].get() is None:
                raise BrowserQuestion(ValueTransient("sca_code", masked=True))
            return list(self.accounts)

    engine = FortuneoEngine(
        Credentials("login", "password"),
        SidecarPaths.from_root(tmp_path).ensure(),
        otp_provider=OTPProvider(env={}, interactive=False),
        backend_factory=lambda: ScaBackend([make_account("acc-sca")]),
    )

    with pytest.raises(Exception, match="validation 2FA"):
        engine.accounts()
