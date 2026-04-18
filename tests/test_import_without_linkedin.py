import importlib
import os
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch


class ImportWithoutLinkedInTests(unittest.TestCase):
    def test_importing_and_running_main_without_linkedin_env_does_not_crash(self) -> None:
        module_names = [
            "index",
            "agent.config.settings",
            "agent.config.agent_config",
            "agent.core.database",
            "agent.core.github",
            "agent.core.linkedin",
            "agent.core.llm",
        ]
        saved_modules = {name: sys.modules.get(name) for name in module_names}

        for name in module_names:
            sys.modules.pop(name, None)

        base_env = {
            "GITHUB_TOKEN": "github-token",
            "GITHUB_USERNAME": "test-user",
            "OPENROUTER_API_KEY": "openrouter-token",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_KEY": "supabase-service-key",
        }

        try:
            with patch.dict(os.environ, base_env, clear=True):
                with patch("dotenv.load_dotenv", return_value=False):
                    with patch("supabase.create_client", return_value=Mock()):
                        index_module = importlib.import_module("index")

            with patch.object(index_module, "check_openrouter_connection", AsyncMock(return_value=False)):
                index_module.main()
        finally:
            for name in module_names:
                sys.modules.pop(name, None)
            for name, module in saved_modules.items():
                if module is not None:
                    sys.modules[name] = module
