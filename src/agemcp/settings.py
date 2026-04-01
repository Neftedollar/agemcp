"""
Simplified Pydantic v2 settings configuration for Multi-Database MCP Server.
Supports .env files, environment variables, and runtime validation with DSN-based configuration.
"""



from pathlib import Path
from typing import Any, Dict, List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agemcp.database_connection_settings import DatabaseConnectionSettings
from agemcp.environment import Environment


ENV_FILE_PATH = Environment.get_dotenv_path()
ENV_FILE_DIR_PATH = Path(ENV_FILE_PATH).parent

class AppSettings(BaseSettings):
    """Main application configuration."""

    log_level: str = Field(default="INFO", description="Logging level", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    
    @property
    def package_path(self) -> Path: return Path(__file__).parent


class McpSettings(BaseSettings):
    """MCP Server configuration."""
    port: int = Field(default=7999, description="MCP server port")
    host: str = Field(default="0.0.0.0", description="MCP server host")
    transport: str = Field(
        default="stdio",
        description="MCP server transport protocol",
        pattern=r"^(sse|streamable-http|stdio)$"
    )
    log_level: str = Field(
        default="DEBUG",
        description="MCP server log level",
        pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"
    )

    
class DbSettings(BaseSettings):

    model_config = SettingsConfigDict( env_nested_delimiter='__')

    dsn: str
    echo: bool | None         = Field(default=None)
    pool_min_connections: int = Field(default=5)
    pool_max_connections: int = Field(default=10)
    pool_max_overflow: int    = Field(default=20)

    @property
    def connections(self) -> Dict[str, DatabaseConnectionSettings]:
        dcs = DatabaseConnectionSettings.from_name_and_dsn( "primary", self.dsn )
        dcs.pool_min_connections = self.pool_min_connections
        dcs.pool_max_connections = self.pool_max_connections
        dcs.pool_max_overflow = self.pool_max_overflow

        return {
            "primary": dcs
        }

    def get_primary(self) -> DatabaseConnectionSettings:
        if primary := self.connections.get("primary", None):
            return primary
        raise ValueError("Primary database connection is not defined or is invalid.")
    
    
    
        

class AgeSettings(BaseSettings):
    """AGE-specific configuration."""
    ident_property: str
    start_ident_property: str
    end_ident_property: str


class Settings(BaseSettings):
    """Complete application settings with multi-database support."""
    
    model_config = SettingsConfigDict(
        env_file=(ENV_FILE_PATH),
        env_file_encoding='utf-8',
        env_nested_delimiter='__',
    )
    
    app: AppSettings
    mcp: McpSettings
    db: DbSettings
    age: AgeSettings
    env: Environment = Field(default_factory=Environment.current, description="Current application environment")
    
    def primary_database(self) -> DatabaseConnectionSettings:
        """Retrieve the primary database connection settings."""
        return self.db.get_primary()
    
class _SettingsTesting(Settings):
    """Settings for testing environment."""
    
    model_config = SettingsConfigDict(
        env_file=(str(ENV_FILE_DIR_PATH / '.env.testing')),
        env_file_encoding='utf-8',
        env_nested_delimiter='__',
    )
    env: Environment = Field(default_factory=lambda: Environment("testing"), description="Current application environment")

class _SettingsDevelopment(Settings):
    """Settings for development environment."""
    
    model_config = SettingsConfigDict(
        env_file=(str(ENV_FILE_PATH)),
        env_file_encoding='utf-8',
        env_nested_delimiter='__',
    )
    env: Environment = Field(default_factory=lambda: Environment("development"), description="Current application environment")


# Global settings singleton
SETTINGS: Dict[Environment,Settings] = {}

def get_settings() -> Settings:
    """Retrieve the global settings singleton with lazy environment configuration.
    
    Implements a singleton pattern that configures Pydantic settings based on the current
    environment when first accessed. The environment file and nested delimiter configuration
    are applied dynamically, allowing runtime environment changes before first access.
    
    Returns:
        Settings: The configured global settings instance
        
    Example:
        >>> # Basic usage:
        >>> settings = get_settings()
        >>> db_config = settings.primary_database()
        ...
        >>> # With environment override:
        >>> from agemcp.environment import set_current_env
        >>> set_current_env('testing')
        >>> settings = get_settings()  # Uses .env.testing file
        
    Note:
        Environment must be set before first call to take effect. Subsequent calls
        return the cached instance regardless of environment changes.
    """
    current_env = Environment.current()
    global SETTINGS
    if SETTINGS.get(current_env, None) is None:
        if current_env.is_testing():
            SETTINGS[Environment.TESTING] = _SettingsTesting()  # pyright: ignore
        elif current_env.is_development():
            SETTINGS[Environment.DEVELOPMENT] = _SettingsDevelopment()  # pyright: ignore
        elif current_env.is_production() or current_env.is_staging():
            SETTINGS[current_env] = Settings()  # pyright: ignore
        else:
            raise ValueError(f"Unsupported environment: {current_env.value}.")

    settings = SETTINGS.get(current_env, None)
    if settings is None:
        raise ValueError(f"Settings for environment {current_env.value} are not initialized.")

    return settings

