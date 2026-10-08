import io
import sys
import pytest
from unittest.mock import patch, MagicMock

from sling_querybuilder.cli import main, create_parser


class TestCLI:
    def test_parser_compile_command(self):
        parser = create_parser()
        args = parser.parse_args(["compile", "path=/content&type=cq:Page", "--selector", "node"])
        assert args.command == "compile"
        assert args.query == "path=/content&type=cq:Page"
        assert args.selector == "node"

    def test_parser_gateway_defaults(self):
        parser = create_parser()
        args = parser.parse_args(["gateway"])
        assert args.command == "gateway"
        assert args.sling_url == "http://localhost:8080"
        assert args.auth == "admin:admin"
        assert args.host == "0.0.0.0"
        assert args.port == 8081

    def test_cli_compile_execution(self, capsys):
        exit_code = main(["compile", "path=/content/meridian&type=cq:Page&p.limit=15"])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "SELECT [n].* FROM [cq:Page] AS [n]" in captured.out
        assert "ISDESCENDANTNODE([n], '/content/meridian')" in captured.out
        assert "Limit:  15" in captured.out
        assert "Offset: 0" in captured.out

    @patch("sling_querybuilder.cli.QueryBuilderGateway")
    def test_cli_gateway_execution(self, mock_gw_cls):
        mock_gw_instance = MagicMock()
        mock_gw_cls.return_value = mock_gw_instance

        # Simulate KeyboardInterrupt inside start
        mock_gw_instance.start.side_effect = KeyboardInterrupt

        exit_code = main(["gateway", "--sling-url", "http://10.0.0.142:8080", "--port", "9090"])
        assert exit_code == 0
        mock_gw_cls.assert_called_once_with(
            sling_base_url="http://10.0.0.142:8080",
            sling_auth=("admin", "admin"),
            host="0.0.0.0",
            port=9090,
        )
        mock_gw_instance.start.assert_called_once_with(blocking=True)
        mock_gw_instance.stop.assert_called_once()
