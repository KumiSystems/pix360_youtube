from pix360core.classes import DownloaderModule, HTTPRequest, DownloadError, DEFAULT_CUBEMAP_TO_EQUIRECTANGULAR_STITCHER, DEFAULT_STITCHER
from pix360core.models import Conversion, File

from django.core.files.base import ContentFile

from typing import List, Tuple, Dict

import re
import logging
import uuid

class KRPanoDownloader(DownloaderModule):
    name: str = "KRPano Downloader"
    identifier: str = "systems.kumi.pix360.krpano"

    def __init__(self):
        self.logger = logging.getLogger("pix360")

    REGEX_FULL: List[Tuple[str, int, Dict[str, str]]] = [
            (r"\d+/\d+/\d+_\d+\.jpg", DownloaderModule.CERTAINTY_PROBABLE, {}),    
            ]

    REGEX_SIMPLE: List[Tuple[str, int, Dict[str, str]]] = [
            (r"\_[frblud].jpg", DownloaderModule.CERTAINTY_PROBABLE, {}),
            (r"^\d.jpg", DownloaderModule.CERTAINTY_POSSIBLE, {"tiles": "012345"}),
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
        
        for regex, certainty, kwargs in cls.REGEX_FULL:
            if bool(re.search(regex, url)):
                return certainty

        for regex, certainty, kwargs in cls.REGEX_SIMPLE:
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
        converter = KRPanoConverter(conversion)
        result = converter.to_equirectangular()
        self.logger.debug(f"Finished processing conversion {conversion.id} with URL {conversion.url}. Result: {result.id}")
        return result

class KRPanoConverter:
    def __init__(self, conversion):
        self.conversion = conversion

        self.logger = logging.getLogger("pix360")

        self.cubemap_stitcher = DEFAULT_CUBEMAP_TO_EQUIRECTANGULAR_STITCHER()
        self.stitcher = DEFAULT_STITCHER()

    def url_normalize(self, url):
        '''
        Takes the URL of any image in a krpano panorama and returns a string
        with substitutable variables for image IDs.

        :param url: URL of an image contained in a krpano panorama
        :return: string with substitutable variables or False if URL invalid
        '''

        try:
            with HTTPRequest(url).open() as res:
                assert res.getcode() == 200

            parts = url.split("/")

            assert "_" in parts[-1]
            parts[-1] = "%i_%i.jpg"
            parts[-2] = "%i"
            parts[-3] = parts[-3].rstrip("0123456789") + "%i"

            return "/".join(parts)

        except Exception as e:
            return False

    def get_max_zoom(self, schema):
        '''
        Takes a normalized string from krpano_normalize() and returns the maximum
        zoom level available.

        :param schema: normalized URL format output by krpano_normalize()
        :return: int value of largest available zoom level
        '''

        self.logger.debug(f"Entering get_max_zoom for {schema}")

        l = 0

        while True:
            try:
                url = schema % (0, l+1, 0, 0)
                with HTTPRequest(url).open() as res:
                    assert res.getcode() == 200
                    l += 1
            except:
                self.logger.debug(f"Max zoom is {l}")
                return l

    def export(self, schema):
        '''
        Takes a normalized string from krpano_normalize() and returns a list of
        lists of lists containing all images fit for passing into stitch().

        :param schema: normalized URL format output by krpano_normalize()
        :return: list of lists of lists of PIL.Image() objects for multistitch()
        '''

        self.logger.debug(f"Entering export for {schema}")

        maxzoom = self.get_max_zoom(schema)
        output = []

        for tile in range(6):
            t_array = []
            y = 0

            while True:
                r_array = []
                x = 0

                while True:
                    try:
                        res = HTTPRequest(schema % (tile, maxzoom, y, x)).open()
                        assert res.getcode() == 200
                        content = res.read()
                        fo = ContentFile(content, name=f"{tile}_{maxzoom}_{y}_{x}.jpg")
                        file = File.objects.create(conversion=self.conversion, file=fo, mime_type="image/jpeg")
                        r_array.append(file)
                        x += 1
                    except Exception as e:
                        self.logger.debug(f"Error: {e}")
                        break

                if not r_array:
                    break

                t_array.append(r_array)
                y += 1

            output.append(t_array)

        return output

    def export_simple(self, url, tiles="frblud"):
        '''
        Exports krpano panoramas which only consist of six complete tiles. Takes
        the URL of one of these images and returns a list of PIL.Image objects

        :param url: URL of one of the images
        :return: list of PIL.Image objects
        '''

        self.logger.debug(f"Entering export_simple for {url}")

        output = []

        for i in tiles:
            cur = url[:-5] + i + url[-4:]
            res = HTTPRequest(cur).open()
            assert res.getcode() == 200
            fo = ContentFile(res.read())
            file = File.objects.create(conversion=self.conversion, file=fo, mime_type="image/jpeg")
            output += [file]

        return output

    def export_full(self, url: str) -> File:
        self.logger.debug(f"Entering export_full for {url}")
        
        schema = self.url_normalize(url)
        images = self.export(schema)
        return self.stitcher.multistitch(images)

    def make_tiles(self, url):
        '''
        Determines the type of processing needed to build the six tiles, then
        creates and returns them.

        :param url: URL of any image in a krpano panorama
        :return: list of stitched PIL.Image objects (back, right, front, left, top,
                bottom)
        '''

        self.logger.debug(f"Entering make_tiles for {url}")

        for regex, certainty, kwargs in KRPanoDownloader.REGEX_FULL:
            if bool(re.search(regex, url)):
                return self.export_full(url, **kwargs)

        for regex, certainty, kwargs in KRPanoDownloader.REGEX_SIMPLE:
            if bool(re.search(regex, url)):
                return self.export_simple(url, **kwargs)

        raise ValueError("%s does not seem to be a valid krpano URL." % url)

    def to_equirectangular(self):
        '''
        Takes the URL of any image in a krpano panorama and returns a finished
        stitched image.

        :param url: Image URL
        :return: PIL.Image object containing the final image
        '''

        self.logger.debug(f"Entering to_equirectangular for {self.conversion.url}")
        stitched = self.make_tiles(self.conversion.url)
        self.logger.debug(f"Calling cubemap_to_equirectangular for {self.conversion.url}")

        if self.conversion.properties:
            rotation = self.conversion.properties.get("rotation", (0,0,0))
        else:
            rotation = (0,0,0)

        function = self.cubemap_stitcher.cubemap_to_equirectangular
        return function(stitched, rotation)
