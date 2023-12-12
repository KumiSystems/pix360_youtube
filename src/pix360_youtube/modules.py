from pix360core.classes import DownloaderModule, HTTPRequest, DownloadError, DEFAULT_CUBEMAP_TO_EQUIRECTANGULAR_STITCHER, DEFAULT_STITCHER
from pix360core.models import Conversion, File

from django.core.files.base import ContentFile

from typing import List, Tuple, Dict

import re
import logging
import uuid
import tempfile
import pathlib

import yt_dlp

class YouTubeDownloader(DownloaderModule):
    name: str = "YouTube Downloader"
    identifier: str = "systems.kumi.pix360.youtube"

    def __init__(self):
        self.logger = logging.getLogger("pix360")

    REGEX: List[Tuple[str, int, Dict[str, str]]] = [
            (r"^https://(www\.)?youtube.com", DownloaderModule.CERTAINTY_PROBABLE),    
            ]

    @classmethod
    def test_url(cls, url: str) -> int:
        """Test if URL looks like this module can handle it

        Args:
            url (str): URL to test

        Returns:
            int: Certainty level of the URL being supported by this module
                 CERTAINTY_UNSUPPORTED if the URL is not supported at all
                 CERTAINTY_POSSIBLE if the URL may be supported
                 CERTAINTY_PROBABLE if the URL is probably supported
        """
        
        for regex, certainty in cls.REGEX:
            if bool(re.search(regex, url)):
                return certainty

        return DownloaderModule.CERTAINTY_UNSUPPORTED

    def process_conversion(self, conversion: Conversion) -> File:
        """Download content from the given URL

        Args:
            conversion (Conversion): Conversion object to process

        Raises:
            DownloadError: If an error occurred while downloading content

        Returns:
            File: File object containing the downloaded file
        """
        self.logger.debug(f"Processing conversion {conversion.id} with URL {conversion.url}")
        converter = YouTubeConverter(conversion)
        result = converter.convert()
        self.logger.debug(f"Finished processing conversion {conversion.id} with URL {conversion.url}. Result: {result.id}")
        return result

class YouTubeConverter:
    def __init__(self, conversion):
        self.conversion = conversion
        self.logger = logging.getLogger("pix360")

    def convert(self):
        self.logger.debug(f"Entering convert for {self.conversion.url}")
        file = self.download()
        file.is_result = True
        file.save()
        return file

    def hook(self, d):
        if d["status"] == "finished":
            self.logger.debug(f"Finished downloading {self.conversion.url}")
        elif d["status"] == "downloading":
            self.logger.debug(f"Downloading {self.conversion.url}: {d['filename']} ({d['_percent_str']})")

    def download(self):
        self.logger.debug(f"Entering download for {self.conversion.url}")
        yt_dlp.utils.std_headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
                                                    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

        with tempfile.TemporaryDirectory() as outdir:
            ydl_opts = {
                "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "outtmpl": f"{outdir}/%(id)s.%(ext)s",
                "logger": self.logger,
                "progress_hooks": [self.hook],
                "merge_output_format": "mp4",
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.conversion.url])
            
            # Read the file
            file = pathlib.Path(outdir).glob("*.mp4")
            file = next(file)

            self.logger.debug(f"Finished downloading {self.conversion.url}. File: {file}")

            # Create a File object
            with open(file, "rb") as f:
                fo = ContentFile(f.read(), name="result.mp4")
                file = File.objects.create(conversion=self.conversion, file=fo, mime_type="video/mp4")

            return file
