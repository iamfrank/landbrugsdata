from abc import ABC, abstractmethod
from google.cloud import storage
import geopandas as gpd
from shapely.geometry import shape
import pyarrow as pa
import logging
from typing import Optional, Dict, Any
import time
import os
import pandas as pd
import tempfile
from contextlib import contextmanager
from .utils.geometry_validator import validate_and_transform_geometries

logger = logging.getLogger(__name__)

class BaseSource(ABC):
    """Base class for all data sources that fetch and store raw data"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.storage_client = storage.Client()
        self.bucket = self.storage_client.bucket(config.get('bucket', 'landbrugsdata-raw-data'))
    
    @contextmanager
    def get_temp_file(self):
        """Create a temporary file that's automatically cleaned up"""
        temp = tempfile.NamedTemporaryFile(delete=False)
        try:
            yield temp
        finally:
            temp.close()
            os.unlink(temp.name)
    
    @property
    @abstractmethod
    def source_id(self) -> str:
        """Unique identifier for this source"""
        pass
        
    @abstractmethod
    def fetch(self) -> pd.DataFrame:
        """Fetch raw data from source"""
        pass
        
    def store(self, df: pd.DataFrame, dataset: str = None) -> bool:
        """Store raw data in GCS"""
        try:
            # Use dataset name if provided, otherwise use source_id
            path = f"raw/{dataset or self.source_id}/current.parquet"
            
            with self.get_temp_file() as temp_file:
                df.to_parquet(temp_file.name)
                self.bucket.blob(path).upload_from_filename(temp_file.name)
                
            return True
            
        except Exception as e:
            logger.error(f"Error storing data for {dataset or self.source_id}: {str(e)}")
            return False
            
    def sync(self) -> Optional[int]:
        """Full sync process: fetch and store"""
        try:
            df = self.fetch()
            if self.store(df):
                return len(df)
            return None
        except Exception as e:
            logger.error(f"Sync failed for {self.source_id}: {str(e)}")
            return None 
    
    def _upload_to_storage(self, df: pd.DataFrame, table_name: str) -> None:
        """Upload DataFrame to Cloud Storage as parquet file"""
        if df.empty:
            logger.warning(f"Empty DataFrame for {table_name}, skipping upload")
            return
            
        try:
            # Save to temp file
            temp_file = f"/tmp/{table_name}.parquet"
            df.to_parquet(temp_file, index=False)
            
            # Upload to Cloud Storage
            blob = self.bucket.blob(f'raw/{self.source_id}/{table_name}.parquet')
            blob.upload_from_filename(temp_file)
            
            # Clean up
            os.remove(temp_file)
            
            logger.info(f"Uploaded {len(df):,} records to {table_name}")
            
        except Exception as e:
            logger.error(f"Error uploading {table_name} to storage: {str(e)}")
            raise

class GeospatialSource(BaseSource):
    """Base class for geospatial data sources that fetch and store raw data"""
    
    async def store(self, df: pd.DataFrame, dataset: str = None) -> bool:
        """Store raw data in GCS"""
        try:
            # Use dataset name if provided, otherwise use source_id
            path = f"raw/{dataset or self.source_id}/current.parquet"
            
            with self.get_temp_file() as temp_file:
                if isinstance(df, gpd.GeoDataFrame):
                    df.to_parquet(temp_file.name)
                else:
                    # Convert to GeoDataFrame if it's not already
                    gdf = gpd.GeoDataFrame(df)
                    gdf = validate_and_transform_geometries(gdf, dataset or self.source_id)
                    gdf.to_parquet(temp_file.name)
                
                self.bucket.blob(path).upload_from_filename(temp_file.name)
                
            return True
            
        except Exception as e:
            logger.error(f"Error storing data for {dataset or self.source_id}: {str(e)}")
            return False 