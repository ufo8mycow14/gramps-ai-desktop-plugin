"""Document-local image assets; never convert beside originals or shared caches."""
import hashlib
import json
from pathlib import Path


def inputs(db):
    from gramps.gen.utils.file import media_path_full
    result = []
    for handle in sorted(db.get_media_handles()):
        media = db.get_media_from_handle(handle)
        if not media.get_mime_type().startswith('image') or not media.get_path():
            continue
        path = Path(media_path_full(db, media.get_path())).resolve()
        if path.is_file():
            checksum = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    checksum.update(chunk)
            result.append({'handle': handle, 'path': str(path), 'sha256': checksum.hexdigest()})
        else:
            result.append({'handle': handle, 'path': str(path), 'sha256': None})
    return result


class Assets:
    def __init__(self, folder, source_inputs):
        self.folder = Path(folder)
        self.sources = {item['path']: item['sha256'] for item in source_inputs}
        self.dimensions = {}

    def read(self, path):
        path = Path(path).resolve()
        if str(path) not in self.sources or self.sources[str(path)] is None:
            raise ValueError('Report requested an unreviewed or missing image')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.sources[str(path)]:
            raise ValueError('Source image changed during generation')
        return raw

    def asset(self, path, rectangle, thumbnail=False):
        path = Path(path).resolve()
        raw = self.read(path)
        key = hashlib.sha256(json.dumps([self.sources[str(path)], rectangle, thumbnail]).encode('utf-8')).hexdigest()
        relative = Path('media') / (key + ('.png' if thumbnail else '.jpg'))
        destination = self.folder / relative
        if destination.exists():
            return relative.as_posix()
        destination.parent.mkdir(exist_ok=True)
        if rectangle is not None and (len(rectangle) != 4 or any(type(v) not in (int, float) or not 0 <= v <= 100 for v in rectangle)):
            raise ValueError('Image crop must contain four percentage coordinates')
        if thumbnail:
            from gramps.plugins.thumbnailer.imagethumb import ImageThumb
            from gramps.gen.const import SIZE_NORMAL
            staging_input = self.folder / ('.input-' + key + path.suffix)
            try:
                staging_input.write_bytes(raw)
                if not ImageThumb().run('', str(staging_input), str(destination), SIZE_NORMAL, rectangle):
                    raise ValueError('Native isolated thumbnail generation failed')
            finally:
                staging_input.unlink(missing_ok=True)
        else:
            from gi.repository import GdkPixbuf
            loader = GdkPixbuf.PixbufLoader.new()
            loader.write(raw)
            loader.close()
            image = loader.get_pixbuf()
            self.dimensions[relative.as_posix()] = (image.get_width(), image.get_height())
            if rectangle is not None:
                x1, y1, x2, y2 = rectangle
                left, top = int(min(x1, x2) * image.get_width() / 100), int(min(y1, y2) * image.get_height() / 100)
                width, height = int(abs(x2 - x1) * image.get_width() / 100), int(abs(y2 - y1) * image.get_height() / 100)
                if width > 0 and height > 0:
                    image = image.new_subpixbuf(left, top, width, height)
            if image.get_has_alpha():
                image = image.composite_color_simple(image.get_width(), image.get_height(),
                            GdkPixbuf.InterpType.BILINEAR, 255, 8, 0xffffff, 0xffffff)
            image.savev(str(destination), 'jpeg', ['quality'], ['95'])
        return relative.as_posix()


def configure(doc, folder, source_inputs, category_tree=False):
    assets = Assets(folder, source_inputs)
    if category_tree:
        from gramps.gen.lib import Person
        from gramps.gen.utils.file import media_path_full
        original_node = doc.write_node
        def write_node(db, level, node_type, person, marriage_flag, option_list=None):
            image = None
            for reference in person.get_media_list():
                media = db.get_media_from_handle(reference.ref)
                if media.get_mime_type().startswith('image'):
                    path = Path(media_path_full(db, media.get_path()))
                    if path.is_file():
                        image = assets.asset(path, reference.get_rectangle(), thumbnail=True)
                        break
            clone = Person(person.serialize())
            clone.set_media_list([])
            original_write = doc.write
            def write(node_level, text):
                if image and node_level == level and text == '}\n':
                    original_write(level + 1, 'image = {%s},\n' % image)
                return original_write(node_level, text)
            doc.write = write
            try:
                return original_node(db, level, node_type, clone, marriage_flag, option_list)
            finally:
                doc.write = original_write
        doc.write_node = write_node
    else:
        original_media = doc.add_media
        # Already-created JPEGs need no Pillow. Give this one native bound method
        # an isolated flag so a missing optional Pillow cannot emit false errors.
        import types
        function = original_media.__func__
        if 'HAVE_PIL' in function.__globals__:
            function = types.FunctionType(function.__code__, {**function.__globals__, 'HAVE_PIL': True},
                                          function.__name__, function.__defaults__, function.__closure__)
            original_media = types.MethodType(function, doc)
        def add_media(infile, pos, x, y, alt='', style_name=None, crop=None):
            asset = assets.asset(infile, crop)
            if Path(infile).suffix.lower() not in ('.jpg', '.jpeg', '.png'):
                width, height = assets.dimensions[asset]
                if height > width:
                    y = y * height / width  # Preserve native non-JPEG portrait sizing.
            # Native LaTeX may derive a sibling JPEG from its input. Supplying an
            # already-created, safe relative JPEG avoids conversion and temp paths.
            return original_media(asset, pos, x, y, alt, style_name, crop)
        doc.add_media = add_media
    return assets
