#!/usr/bin/env python
#
# Copyright (C) 2002 Gre7g Luterman <gre7g@wolfhome.com>
#
# This file is part of TMDA.
#
# TMDA is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  A copy of this license should
# be included in the file COPYING.
#
# TMDA is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
# for more details.
#
# You should have received a copy of the GNU General Public License
# along with TMDA; if not, write to the Free Software Foundation, Inc.,
# 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA

"Generic session handler."

import ConfigParser
import glob
import os
import pickle
import pwd
import random
import re
import sys
import time
from types import *

import CgiUtil
import MyCgiTb
import Template

from TMDA import Util

class Session:
  """Sessioning tool for use in CGI.

Each time the user requests a web page, the page is generated by a new thread of
the main program, without any inherent visibility to threads which have been
executed previously.  This object provides a means to easily save and restore
values from one page hit to another.  For example, if the user clicks the
"toggle headers" button while viewing an e-mail, this new setting may be saved
in the session and checked the next time an e-mail is viewed so that the user
won't have to choose "toggle headers" every time.

Session data is stored in two seperate files.  The first file contains very
little information and is saved at /tmp/TMDASess.XXXXXXXX where XXXXXXXX is a
random session ID (SID) string.  This SID is passed from web page to web page
and allows the Session class to track which user a request came from (since
multiple users could be using tmda-cgi simultaneously).

The second file is user specific and has a filename based on the
Defaults.CGI_SETTINGS variable.  This file stores the bulk of the session data.
Note that since there is only one of these files per user login.  If a user logs
in from multiple browsers, even though they will use two different session IDs,
the same session data will be available to both.

Resurrect an old session by passing a form with the session ID (SID) into the
constructor.  An empty object will be created if the session has expired.  A new
session will be created if the form does not specify a (valid) SID.

Instantiating a session will check Form["user"] and Form["password"] if they
exist to establish a valid session.  To check the validity of a session, test
Object.Valid before any action that might reveal sensative information.

Once a session is validated, the module will switch UID to Session["UID"].

The session's SID is saved as a member called SID.

Session data is stored in the object by treating it as a dictionary.  For
example:
  A = Session(Form)
  A['key'] = value
  A.Save()
  print "http://some/url?SID=%s" % A.SID

The Save() member saves the session's current values on disk, but can only be
called once a validated session has been established.  Writes to the
/tmp/TMDASess.XXXXXXXX file are done as the base RUID.  Writes to the
Defaults.CGI_SETTINGS file are done as the user's UID.

Indexies to the class may be simple strings or two element arrays or tuples.
Reading a value will first check previously saved values for an index match,
then if it cannot find a match, it will check the theme.ini file for a match
(where the two elements represent a section and option, respectively) before
giving up an throwing an exception.
"""

  # Globals
  Rands = random.Random()
  RealUser = 0
  ThemesDir = os.path.join(os.getcwd(), "display", "themes")
  Valid = 0

  def __suid__(self, User):
    """Try to change to a new user.

User can be "root", "web", or "user".  "root" attempts to setuid to root.
"web" attempts to seteuid to the base RUID.  "user" attempt to setuid to
Vars["UID"].  An exception will be thrown if __suid_ is called after
__suid__("user").  __suid__ reports an error if we can't change IDs, but should
be able to."""

    # Don't allow "user" to be called twice
    if User == "user":
      if self.RealUser: raise OSError, "Cannot setuid twice."
      self.RealUser = 1

    if os.environ["TMDA_CGI_MODE"] == "system-wide":
      try:
        # First, strip off other users
        os.seteuid(0)
        os.setegid(0)

        # If they wanted "root", we're done
        if User == "root":
          os.setuid(0)

        # If they want "web", go find out who that is
        elif User == "web":
          PasswordRecord = pwd.getpwuid(WebUID)
          UID = PasswordRecord[2]
          GID = PasswordRecord[3]
          if not UID:
            CgiUtil.TermError("CGI_USER is UID 0.", "It is not safe to allow "
              "root to process session files.", "set euid",
              "", "Do not run your webserver as root.")
          os.setegid(GID)
          os.seteuid(UID)

        # If they want "user", go do it
        elif User == "user":
          os.setgid(int(self.Vars["GID"]))
          os.setuid(int(self.Vars["UID"]))

      except OSError:
        CgiUtil.TermError("Cannot SUID.", "File permissions on the CGI have "
          "been changed or the CGI is located in a nosuid partition.",
          "set euid", "", """Recheck the CGI's permissions and owner.  The file
permissions should be 6711 (-rws--s--x) and the owner should be root.<br>Also
check in which partition you placed the CGI.  You cannot run the CGI in
system-wide mode if its partition is marked "nosuid" in /etc/fstab.""")

    else:
      if not os.geteuid():
        if os.environ["TMDA_CGI_MODE"] == "single-user":
          Detail = """The file permissions should be 6711 (-rws--s--x) and the
owner should <b><i>not</i></b> be root."""
        else:
          Detail = "The file permissions should be 711 (-rwx--x--x)."
        CgiUtil.TermError("Running as root.", "CGI should not be running as "
          "root. This is unsafe.", "set euid", "",
          "Recheck the CGI's permissions and owner.  %s" % Detail)

  def Save(self):
    """Save all session variables to disk.  Global RealUser determines whether
we save Vars or PVars."""

    CWD = os.getcwd()
    if self.RealUser:
      # Not sure why I have to refer to Defaults via globals(), but it works
      if globals().has_key("Defaults") and \
        (type(globals()["Defaults"]) == DictType):
        os.chdir(os.path.split(globals()["Defaults"]["TMDARC"])[0])
        Filename = globals()["Defaults"]["CGI_SETTINGS"]
      else:
        from TMDA import Defaults
        os.chdir(os.path.split(Defaults.TMDARC)[0])
        Filename = Defaults.CGI_SETTINGS
      Data = self.PVars
    else:
      self.__suid__("web")
      Filename = os.environ["TMDA_SESSION_PREFIX"] + self.SID
      Data     = self.Vars
      UMask    = os.umask(0177)

    # Save data
    try:
      F = open(Filename, "w")
      pickle.dump(Data, F)
      F.close()
    except IOError:
      CgiUtil.TermError("Unable to save session data.",
        "Insufficient privileges.", "write session file",
        "%s<br>%s" % (CgiUtil.FileDetails("Session file", Filename),
        CgiUtil.FileDetails("CWD", os.getcwd())),
        """Either grant the session user sufficient privileges to write the
session file,<br>or recompile the CGI and specify a CGI_USER with more
rights.""")
    if not self.RealUser:
      os.umask(UMask)
    os.chdir(CWD)

  def LoadSysDefaults(self):
    "Load system defaults and trim domain name (if present) off of user name."
    # Get system defaults
    self.PVars = {}
    self.ThemeVars = ConfigParser.ConfigParser()
    # Make this case-sensitive
    self.ThemeVars.optionxform = str
    Filename  = os.path.join(os.getcwd(), "defaults.ini")
    self.ThemeVars.read(Filename)
    if len(self.ThemeVars.sections()) < 2:
      CgiUtil.TermError("Missing defaults.ini", "Cannot load defaults.ini",
        "import defaults", "%s<br>%s" % (
        CgiUtil.FileDetails("Theme settings", Filename),
        CgiUtil.FileDetails("Defaults settings", Default)),
        "Download a new copy of tmda-cgi.")

    # Extract out section "NoOverride"
    self.NoOverride = {}
    for Option in self.ThemeVars.options("NoOverride"):
      self.NoOverride[Option] = self.ThemeVars.get("NoOverride", Option, 1)

    # Trim out domain name from user name
    os.environ["LOGIN"] = self.Vars["User"]
    Match = re.search(self[("NoOverride", "UserSplit")], self.Vars["User"])
    if Match:
      os.environ["USER"] = Match.group(1)
      os.environ["LOGNAME"] = Match.group(1)
    else:
      os.environ["USER"] = self.Vars["User"]
      os.environ["LOGNAME"] = self.Vars["User"]

    # Clean up
    self.CleanUp()

  def mylistdir(self):
    """A specialized version of os.listdir() that ignores files that
    start with a leading period."""
    filelist = os.listdir(self.ThemesDir)
    return [x for x in filelist if not (x.startswith('.'))]

  def GetTheme(self):
    "Set up current theme."

    # If user doesn't have a theme or the theme has been deleted, get the first
    # one (alphabetically)
    if not self.has_key(("General", "Theme")) or not os.access \
      (os.path.join(self.ThemesDir, self[("General", "Theme")]), os.R_OK):
      # Pick the first theme in the themes directory
      Themes = self.mylistdir()
      Themes.sort()
      self[("General", "Theme")] = Themes[0]

    # Set up the template to use the theme and load the theme's .ini
    ThemeDir = os.path.join(self.ThemesDir, self[("General", "Theme")])
    Filename = os.path.join(ThemeDir, "theme.ini")
    self.ThemeVars.read(Filename)
    Template.Template.BaseDir = os.path.join(ThemeDir, "template")
    Template.Template.Dict["ThemeDir"] = \
      os.path.join(os.environ["TMDA_CGI_DISP_DIR"], "themes",
        self[("General", "Theme")])
    MyCgiTb.ErrTemplate = "prog_err.html"

    # Replace "NoOverride" variables with the originals
    for Option in self.NoOverride.keys():
      self.ThemeVars.set("NoOverride", Option, self.NoOverride[Option])

  def BecomeUser(self):
    "Set up everything to *BE* the user."
    Match = re.search("(.+)/$", self.Vars["HOME"])
    if Match:
      self.Vars["HOME"] = Match.group(1)
    os.environ["HOME"] = self.Vars["HOME"]
    self.__suid__("user")
    self.Valid = 1

    # Now that we know who we are, get our defaults
    from TMDA import Errors
    try:
      from TMDA import Defaults
    except Errors.ConfigError, (ErrStr):
      if self[("NoOverride", "MayInstall")][0].lower() == "y":
        if (os.environ["TMDA_CGI_MODE"] == "no-su"):
          CgiUtil.TermError("Install failed",
            "Install not allowed in no-su mode", "install", "",
            "Either recompile in another mode or install TMDA manually.")
        self.GetTheme()
        raise CgiUtil.NotInstalled, (ErrStr, self)
      T = Template.Template("no-install.html")
      T["ErrMsg"] = ErrStr
      print T
      sys.exit()

    # Read in our PVars
    try:
      CWD = os.getcwd()
      os.chdir(os.path.split(Defaults.TMDARC)[0])
      Filename = Defaults.CGI_SETTINGS
      F = open(Filename)
      self.PVars = pickle.load(F)
      F.close()
    except (IOError, EOFError, OSError):
      pass
    os.chdir(CWD)

    # Get current theme
    Template.Template.Dict["CharSet"] = self[("General", "CSEncoding")]
    self.GetTheme()

  def __init__(self, Form):
    "Reload an existing SID or create a new one."

    global Defaults

    # Existing, valid looking session?
    if Form.has_key("SID") and \
      re.compile("^[a-zA-Z0-9]{8}$").search(Form["SID"].value):
      # Provide SID to templates
      Template.Template.Dict["SID"] = self.SID = Form["SID"].value

      # Resurrect session
      try:
        self.__suid__("web")
        Filename = os.environ["TMDA_SESSION_PREFIX"] + self.SID
        if os.stat(Filename)[4] != os.geteuid():
          CgiUtil.TermError("CGI_USER does not own session file.",
            "Something suspicious is going on here.  This should not happen.",
            "open file",
            CgiUtil.FileDetails("Session data", Filename),
            "No recommendation.")
        try:
          F = open(Filename)
          self.Vars = pickle.load(F)
          F.close()
        except (IOError, EOFError):
          self.Vars = {}

        # Make sure the session has not been hijacked
        if os.environ["REMOTE_ADDR"] != self.Vars["IP"]:
          CgiUtil.TermError("User's IP address has changed.",
            "Your IP address has changed. This is not allowed.",
            "read session data", "%s->%s" %
            (self.Vars["IP"], os.environ["REMOTE_ADDR"]),
            '<a href="%s">Log back in</a>.' % os.environ["SCRIPT_NAME"])

        # Are they logging out?
        if Form.has_key("cmd") and (Form["cmd"].value == "logout"):
          os.unlink(Filename)
          return

        # Touch session file to keep it from getting cleaned too soon
        os.utime(Filename, None)

        # Is there a TMDARC variable?
        if os.environ.has_key("TMDARC"):
          # Yes, replace it
          os.environ["TMDARC"] = os.environ["TMDARC"].replace("/~/",
            "/%s/" % self.Vars["User"])

        # Load system defaults
        self.LoadSysDefaults()

        # Become the user
        self.BecomeUser()

        # Done!
        return

      # Failed to resurrect session, fall through to make new SID
      except (IOError, OSError):
        pass

    # New session
    SessionChars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" \
      "0123456789"
    self.SID = ""
    for i in range(8):
      self.SID += SessionChars[self.Rands.randrange(len(SessionChars))]
    Template.Template.Dict["SID"] = self.SID
    self.Vars = {}

    # Are they logging in?
    if not Form.has_key("user"):
      Template.Template.Dict["ErrMsg"] = "No user name supplied."
      return
    if not Form.has_key("password"):
      Template.Template.Dict["ErrMsg"] = "No password supplied."
      return

    # Get IP, User, UID, & Home directory
    self.Vars["IP"]   = os.environ["REMOTE_ADDR"]
    self.Vars["User"] = Form["user"].value.lower()
    self.__suid__("root")
    try:
      if os.environ.has_key("TMDA_VLOOKUP"):
        VLookup = \
          CgiUtil.ParseString(os.environ["TMDA_VLOOKUP"], self.Vars["User"])
        List = Util.RunTask(VLookup[1:])
        Sandbox = {"User": self.Vars["User"]}
        Filename = os.path.join("stubs", "%s.py" % VLookup[0])
        try:
          execfile(Filename, Sandbox)
        except IOError:
          CgiUtil.TermError("Can't load virtual user stub.",
            "Cannot execute %s" % Filename, "execute stub",
            "TMDA_VLOOKUP = %s" % os.environ["TMDA_VLOOKUP"], "Recompile CGI.")
        Params = Sandbox["getuserparams"](List)
        self.Vars["HOME"], self.Vars["UID"], self.Vars["GID"] = Params[0:3]
        if len(Params) > 3:
          self.Vars["NAME"] = Params[3]
        else:
          self.Vars["NAME"] = None
      else:
        self.Vars["HOME"], self.Vars["UID"], self.Vars["GID"] = \
          Util.getuserparams(self.Vars["User"])
        self.Vars["NAME"] = pwd.getpwuid(self.Vars["UID"])[4]
    except KeyError, str:
      Template.Template.Dict["ErrMsg"] = \
        "Username %s not found in system.\nstr=%s" % (self.Vars["User"], str)
      return
    # When getuserparams returns a UID of 0 or 1, assume it is a virtual user
    if int(self.Vars["UID"]) < 2:
      PasswordRecord = pwd.getpwnam(os.environ["TMDA_VUSER"])
      self.Vars["UID"]  = PasswordRecord[2]
      self.Vars["GID"]  = PasswordRecord[3]
      if not int(self.Vars["UID"]):
        CgiUtil.TermError("TMDA_VUSER is UID 0.", "It is not safe to run "
          "tmda-cgi as root.", "set euid",
          "TMDA_VUSER = %s" % os.environ["TMDA_VUSER"], "Recompile CGI.")

    # Is there a TMDARC variable?
    if os.environ.has_key("TMDARC"):
      # Yes, replace it
      os.environ["TMDARC"] = os.environ["TMDARC"].replace("/~/",
        "/%s/" % self.Vars["User"])

    # Initialize the auth mechanism
    import Authenticate
    try:
      if os.environ.has_key( "TMDA_AUTH_TYPE" ):
        if os.environ["TMDA_AUTH_TYPE"] == "program":
          Authenticate.InitProgramAuth( os.environ["TMDA_AUTH_ARG"] )
        elif os.environ["TMDA_AUTH_TYPE"] == "remote":
          Authenticate.InitRemoteAuth( os.environ["TMDA_AUTH_ARG"] )
        elif os.environ["TMDA_AUTH_TYPE"] == "file":
          Authenticate.InitFileAuth( os.environ["TMDA_AUTH_ARG"] )
      else:
        # Default to regular flat file.
        # Order of preference:
        #   1) $TMDARC/tmda-cgi
        #   2) $HOME/tmda-cgi
        #   3) /etc/tmda-cgi
        if os.environ.has_key("TMDARC"):
          File = os.path.join(os.path.split(os.environ["TMDARC"])[0],
                              "tmda-cgi")
        else:
          File = os.path.join(self.Vars["HOME"], ".tmda/tmda-cgi")
        self.__suid__("root")
        if not Util.CanRead \
        (
          File, int(self.Vars["UID"]), int(self.Vars["GID"]), 0
        ):
          File = "/etc/tmda-cgi"

        Authenticate.InitFileAuth( File )
    except ValueError, err:
      if os.environ.has_key("TMDA_AUTH_TYPE"):
        AuthType = os.environ["TMDA_AUTH_TYPE"]
      else:
        AuthType = "<b><i>not set</i></b>"
      CgiUtil.TermError( "Auth Initialization Failed", "ValueError caught",
        "init auth type %s" % AuthType, err, "Fix the code." )

    # Validate the new session
    if not Authenticate.CheckPassword(Form): return

    # Save session file
    self.Save()

    # Load system defaults
    self.LoadSysDefaults()

    # Become the user
    self.BecomeUser()

    # Signal main program
    raise CgiUtil.JustLoggedIn, ("Successful login", self)

  def CleanUp(self):
    if self.Rands.random() < float(os.environ["TMDA_SESSION_ODDS"]):
      # Go through all sessions and check a-times
      Sessions = glob.glob(os.environ["TMDA_SESSION_PREFIX"] + "*")
      for Session in Sessions:
        try: # these commands could fail if another thread cleans simultaneously
          Stats = os.stat(Session)
          # Expired?
          if Stats[7] + int(os.environ["TMDA_SESSION_EXP"]) < time.time():
            os.unlink(Session)
        except OSError:
          pass

  def __delitem__(self, a):
    if type(a) in [StringType, UnicodeType]:
      if self.PVars.has_key(a):
        del self.PVars[a]
      else:
        del self.Vars[a]
    else:
      ID = ":".join(a)
      if self.PVars.has_key(ID):
        del self.PVars[ID]
      else:
        self.ThemeVars.remove_option(a[0], a[1])

  def __getitem__(self, a):
    if type(a) in [StringType, UnicodeType]:
      if self.PVars.has_key(a):
        return self.PVars[a]
      else:
        return self.Vars[a]
    else:
      ID = ":".join(a)
      if self.PVars.has_key(ID):
        return self.PVars[ID]
      else:
        return self.ThemeVars.get(a[0], a[1], 1)

  def __setitem__(self, a, b):
    if type(a) in [StringType, UnicodeType]:
      self.PVars[a] = b
    else:
      self.PVars[":".join(a)] = b

  def has_key(self, a):
    if type(a) in [StringType, UnicodeType]:
      return self.PVars.has_key(a) or self.Vars.has_key(a)
    else:
      return self.PVars.has_key(":".join(a)) or \
        self.ThemeVars.has_option(a[0], a[1])

  def keys( self ):
    return self.PVars.keys()

  def vars( self, section ):
    return self.ThemeVars.options( section )
