from dataclasses import dataclass, field
from pathlib import Path
import os


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('TOUR_DATA_DIR', 'data')))
    api_key: str = field(default_factory=lambda: os.getenv('TOUR_API_KEY',''))
    auto_refresh: bool = field(default_factory=lambda: os.getenv('TOUR_AUTO_REFRESH','1')=='1')
    hot_interval: int = field(default_factory=lambda: max(300,int(os.getenv('TOUR_HOT_INTERVAL','1800'))))
    catalogue_interval: int = field(default_factory=lambda: max(3600,int(os.getenv('TOUR_CATALOGUE_INTERVAL','21600'))))
    cors_origins: list[str] = field(default_factory=lambda: [x.strip() for x in os.getenv('TOUR_CORS_ORIGINS','').split(',') if x.strip()])
    max_queue: int = 3
    search_timeout: int = 240
    job_retention_seconds: int = field(default_factory=lambda: max(3600,int(os.getenv('TOUR_JOB_RETENTION_SECONDS','86400'))))
    max_saved_jobs: int = field(default_factory=lambda: max(10,int(os.getenv('TOUR_MAX_SAVED_JOBS','1000'))))

    @property
    def db_path(self):
        return Path(self.data_dir)/'tours.sqlite3'
