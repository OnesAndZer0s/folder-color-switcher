#!/usr/bin/python3
# -*- coding: utf-8 -*-

import gettext
import math
import shutil
import gi
import json
import locale
import os
import re

gi.require_version('Gtk', '3.0')
gi.require_version('Nemo', '3.0')

from gi.repository import Nemo, GObject, Gio, GLib, Gtk, Gdk, GdkPixbuf
from wand.image import Image
from wand.color import Color
# i18n
APP = 'folder-color-switcher'
LOCALE_DIR = "/usr/share/locale"
locale.bindtextdomain(APP, LOCALE_DIR)
gettext.bindtextdomain(APP, LOCALE_DIR)
gettext.textdomain(APP)
_ = gettext.gettext

PLUGIN_DESCRIPTION = _('Allows you to change folder colors from the context menu')

import signal
signal.signal(signal.SIGINT, signal.SIG_DFL)


# LOGGING setup:
# By default, we are only logging messages of level WARNING or higher.
# For debugging purposes it is useful to run Nemo/Caja with
# LOG_FOLDER_COLOR_SWITCHER=10 (DEBUG).
import logging
log_level = os.getenv('LOG_FOLDER_COLOR_SWITCHER', None)
if not log_level:
    log_level = logging.WARNING
else:
    log_level = int(log_level)
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)

class ChangeFolderColorBase(object):

    # view[zoom-level] -> icon size
    # Notes:
    # - icon size:    values from nemo/libnemo-private/nemo-icon-info.h (checked)
    # - list view:    icon sizes don't match the defined sizes in nemo-icon-info.h (yet)
    # - compact view: hasn't defined sizes defined in nemo-icon-info.h
    ZOOM_LEVEL_ICON_SIZES = {
        'icon-view'    : [24, 32, 48, 64, 96, 128, 256],
        #'list-view'    : [16, 24, 32, 48, 72, 96,  192], # defined values
        # sizes measured manually for reasons above
        'list-view'    : [16, 16, 24, 32, 48, 72,  96 ],
        'compact-view' : [16, 16, 18, 24, 36, 48,  96 ]
    }

    ZOOM_LEVELS = {
        'smallest' : 0,
        'smaller'  : 1,
        'small'    : 2,
        'standard' : 3,
        'large'    : 4,
        'larger'   : 5,
        'largest'  : 6
    }

    # https://standards.freedesktop.org/icon-naming-spec/icon-naming-spec-latest.html
    KNOWN_DIRECTORIES = {
        GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP): 'user-desktop',
        GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS): 'folder-documents',
        GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD): 'folder-download',
        GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC): 'folder-music',
        GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES): 'folder-pictures',
        GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PUBLIC_SHARE): 'folder-publicshare',
        GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_TEMPLATES): 'folder-templates',
        GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_VIDEOS): 'folder-videos',
        GLib.get_home_dir(): 'user-home'
    }

    def __init__(self):
        self.parent_directory = None

        # view preferences
        self.ignore_view_metadata = False
        self.default_view = None

        self.nemo_settings = Gio.Settings.new("org.nemo.preferences")
        self.nemo_settings.connect("changed::ignore-view-metadata", self.on_ignore_view_metadata_changed)
        self.nemo_settings.connect("changed::default-folder-viewer", self.on_default_view_changed)
        self.on_ignore_view_metadata_changed(None)
        self.on_default_view_changed(None)

        # # Read the JSON files
        # self.styles = {}
        # path = "/usr/share/folder-color-switcher/colors.d"
        # if os.path.exists(path):
        #     for filename in sorted(os.listdir(path)):
        #         if filename.endswith(".json"):
        #             try:
        #                 with open(os.path.join(path, filename)) as f:
        #                     json_text = json.loads(f.read())
        #                     for style_json in json_text["styles"]:
        #                         style_name = style_json["name"]
        #                         for icon_theme_json in style_json["icon-themes"]:
        #                             name = icon_theme_json["theme"]
        #                             self.styles[name] = style_json
        #             except Exception as e:
        #                 print(f"Failed to parse styles from {filename}.")
        #                 print(e)

    def on_ignore_view_metadata_changed(self, settings, key="ignore-view-metadata"):
        self.ignore_view_metadata = self.nemo_settings.get_boolean(key)

    def on_default_view_changed(self, settings, key="default-folder-viewer"):
        self.default_view = self.nemo_settings.get_string(key)

    @staticmethod
    def get_default_view_zoom_level(view="icon-view"):
        zoom_lvl_string = Gio.Settings.new("org.nemo.%s" % view).get_string("default-zoom-level")
        return ChangeFolderColorBase.ZOOM_LEVELS[zoom_lvl_string]

    def get_default_view_icon_size(self):
        zoom_lvl_index = self.get_default_view_zoom_level(self.default_view)
        return ChangeFolderColorBase.ZOOM_LEVEL_ICON_SIZES[self.default_view][zoom_lvl_index]

    @staticmethod
    def get_folder_icon_name(directory):
        return ChangeFolderColorBase.KNOWN_DIRECTORIES.get(directory, 'folder')

    def get_desired_icon_size(self):
        if self.ignore_view_metadata:
            logger.info("Nemo is set to ignore view metadata")
            return self.get_default_view_icon_size()

        logger.info("Nemo is set to apply view metadata")
        return self.get_current_view_icon_size()


    def get_current_view_icon_size(self):
        # get the folder where we are currently in
        if not self.parent_directory:
            return 64

        info = self.parent_directory.get_location().query_info('metadata::*', 0, None)
        meta_view = info.get_attribute_string('metadata::nemo-default-view')

        if meta_view:
            match = re.search("OAFIID:Nemo_File_Manager_(\\w+)_View", meta_view)
            view = match.group(1).lower() + "-view"
        else:
            view = self.default_view

        if view in self.ZOOM_LEVEL_ICON_SIZES.keys():
            # the zoom level is store as string ('0', ... , '6')
            meta_zoom_lvl = info.get_attribute_string("metadata::nemo-%s-zoom-level" % view)

            if not meta_zoom_lvl:
                # if view is set while the conresponding zoom level is not
                # (e.g. user switched views in this folder but never used zoom)
                zoom_level = self.get_default_view_zoom_level(view)
            else:
                zoom_level = int(meta_zoom_lvl)

            icon_size = self.ZOOM_LEVEL_ICON_SIZES[view][zoom_level]
            logger.debug("Icon size for the current view is: %i", icon_size)
            return icon_size

        logger.debug("falling back to defaults")
        return self.get_default_view_icon_size()

    def get_icon_uri_for_color_size_and_scale(self, icon_name: str, color: Gdk.Color, scale) -> str:

        icon_theme = Gtk.IconTheme.new()
        icon_theme.set_custom_theme("custom")
        self.create_folder_color_icon(color)
        if icon_theme is not None:
            icon_info = icon_theme.choose_icon_for_scale([color.to_string(), None], 1, scale, 0)
            if icon_info:
                uri = GLib.filename_to_uri(icon_info.get_filename(), None)
                print("Found icon at URI "+ uri)
                return uri

        print("no dice")
        # logger.debug('No icon "%s" found for color "%s", size %i and scale %i', icon_name, 1, scale)
        return None


    def create_folder_color_icon(self, color:Gdk.Color): 
        dumbass = "/home/tayler/.icons/custom/copy"
        lessDumbass = "/home/tayler/.icons/custom/places"
        self.do_the_funny_color_thing("16", color)
        self.do_the_funny_color_thing("22", color)
        self.do_the_funny_color_thing("24", color)
        self.do_the_funny_color_thing("32", color)
        self.do_the_funny_color_thing("48", color)


    
    def do_the_funny_color_thing(self, path:str, color: Gdk.Color):
        # wand
        # base_color = #FFFF00
        oldPath = os.path.join("/home/tayler/.icons/custom/copy", path+".png")
        newPath = os.path.join("/home/tayler/.icons/custom/places", path, color.to_string()+".png")
        newColor = Color(color.to_string()).hsl()
        oldColor = Color("#FFFF00").hsl()


        diffHue = 2*(newColor[0]-oldColor[0]+.5) #what
        diffSat = 1+newColor[1]-oldColor[1]
        diffBright = 1+newColor[2]-oldColor[2]
        
        with Image(filename=oldPath) as img:
            img.modulate(diffBright*100, diffSat*100, diffHue*100)
            img.save(filename=newPath)
            pass
        pass
    def set_folder_colors(self, folders, color):
        self.parent_directory = folders[0].get_parent_info()
        if color is not None:
            icon_size = self.get_desired_icon_size()
            default_folder_icon_uri = self.get_icon_uri_for_color_size_and_scale('folder', color, icon_size)

            if not default_folder_icon_uri:
                return

        for folder in folders:
            if folder.is_gone():
                continue

            # get Gio.File object
            directory = folder.get_location()
            path = directory.get_path()

            if color is not None:
                icon_uri = default_folder_icon_uri
                icon_name = self.get_folder_icon_name(path)

                if icon_name != 'folder':
                    icon_uri = self.get_icon_uri_for_color_size_and_scale(icon_name, color, icon_size)

                if icon_uri:
                    directory.set_attribute_string('metadata::custom-icon', icon_uri, 0, None)
            else:
                # A color of None unsets the custom-icon
                directory.set_attribute('metadata::custom-icon', Gio.FileAttributeType.INVALID, 0, 0, None)

            # update the directory's modified date to make Nemo/Caja re-render its icon
            os.utime(path, None)


css_colors = b"""
.folder-color-switcher-button,
.folder-color-switcher-restore {
    min-height: 16px;
    min-width: 16px;
    padding: 0;
}
.folder-color-switcher-button {
    border-style: solid;
    border-width: 1px;
    border-radius: 1px;
    border-color: transparent;
}

.folder-color-switcher-button:hover {
    border-color: #9c9c9c;
}

.folder-color-switcher-restore {
    background-color: transparent;
}

.folder-color-switcher-restore:hover {
    background-color: rgba(255,255,255,0);
}
"""

provider = Gtk.CssProvider()
provider.load_from_data(css_colors)
screen = Gdk.Screen.get_default()
Gtk.StyleContext.add_provider_for_screen (screen, provider, 600) # GTK_STYLE_PROVIDER_PRIORITY_APPLICATION

class ChangeFolderColor(ChangeFolderColorBase, GObject.GObject, Nemo.MenuProvider, Nemo.NameAndDescProvider):
    def __init__(self):
        super().__init__()

        logger.info("Initializing folder-color-switcher extension...")

    def menu_activate_cb(self, menu, color, folders):
        # get scale factor from the clicked menu widget (for Hi-DPI)
        self.set_folder_colors(folders, color)

    def menu_activate_set_color_cb(self, menu, folders):

        gtk_color_selection_dialog = Gtk.ColorSelectionDialog.new(_("Select a color"))
        # gtk_color_selection_dialog.set_transient_for(menu.get_toplevel())
        # gtk_color_selection_dialog.set_position(Gtk.WindowPosition.CENTER)
        # gtk_color_selection_dialog.set_modal(True)


        gtk_color_selection = gtk_color_selection_dialog.get_color_selection()
        gtk_color_selection.set_has_opacity_control(False)
        gtk_color_selection.set_has_palette(False)
        gtk_color_selection.connect
        # gtk_color_selection.set_current_color(Gdk.RGBA(0, 0, 0, 1))
        if gtk_color_selection_dialog.run() == Gtk.ResponseType.OK:
            self.menu_activate_cb(menu, gtk_color_selection.get_current_color(), folders)

        gtk_color_selection_dialog.destroy()

    def menu_activate_reset_color_cb(self, menu, folders):
        self.menu_activate_cb(menu, None, folders)

    def get_background_items(self, window, current_folder):
        return

    def get_name_and_desc(self):
        return [("Folder Color Switcher:::%s" % PLUGIN_DESCRIPTION)]

    # Nemo invoke this function in its startup > Then, create menu entry
    def get_file_items(self, window, items_selected):
        if not items_selected:
            # No items selected
            return

        directories = []
        directories_selected = []

        for item in items_selected:
            # Only folders
            if not item.is_directory():
                logger.info("A selected item is not a directory, skipping")
                continue

            logger.debug('URI "%s" is in selection', item.get_uri())

            if item.get_uri_scheme() != 'file':
                return

            directory = item.get_location()
            logger.debug('Valid path selected: "%s"', directory.get_path())
            directories.append(directory)
            directories_selected.append(item)

        if not directories_selected:
            return

        icon_theme_name = Gio.Settings.new("org.cinnamon.desktop.interface").get_string("icon-theme")
        # if icon_theme_name in self.styles:
        # icon_themes = self.styles[icon_theme_name]["icon-themes"]
        locale.setlocale(locale.LC_ALL, '')
        gettext.bindtextdomain('folder-color-switcher')
        gettext.textdomain('folder-color-switcher')
        logger.debug("At least one color supported: creating menu entry")
        item = Nemo.MenuItem(name='ChangeFolderColorMenu::Top')
        items = directories_selected

        set_color_button = Nemo.MenuItem(
                        name="NemoFolderColorExtension::SetColor",
                        label=_('Set Color...')
                    )
        set_color_button.connect('activate', self.menu_activate_set_color_cb, items)
        if len(items) > 1:
            set_color_button.props.tip = (_("Set the color of the selected folders"))
        else:
            set_color_button.props.tip = (_("Set the color of the selected folder"))
        # widget.pack_start(set_color_button, False, False, 1)



        restore_button = Nemo.MenuItem(
                        name="NemoFolderColorExtension::Restore",
                        label=_('Restore Color')
                    )
        restore_button.connect('activate', self.menu_activate_reset_color_cb, items)
        if len(items) > 1:
            restore_button.props.tip =  (_("Restores the color of the selected folders"))
        else:
            restore_button.props.tip = (_("Restores the color of the selected folder"))


        # item.set_widget_a(self.generate_widget(directories_selected))
        # item.set_widget_b(self.generate_widget(directories_selected))
        return Nemo.MenuItem.new_separator('ChangeFolderColorMenu::TopSep'),  \
                set_color_button,                                              \
                restore_button,                                                 \
                Nemo.MenuItem.new_separator('ChangeFolderColorMenu::BotSep')
        # else:
        #     logger.debug("Could not find any supported colors")
        #     return