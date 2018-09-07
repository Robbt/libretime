#!/usr/bin/python

import ConfigParser
import logging
import os
import libretime_watch as libretime_watch

CONFIGFILE="/etc/airtime/airtime.conf"

def read_config():
  """Read airtime configfile"""
  try: 
    Config = ConfigParser.ConfigParser()
    Config.read(CONFIGFILE)
    libretime_watch.config["db_host"]=Config.get('database','host')
    libretime_watch.config["db_name"]=Config.get('database','dbname')
    libretime_watch.config["db_user"]=Config.get('database','dbuser')
    libretime_watch.config["db_pass"]=Config.get('database','dbpass')
    libretime_watch.config["rm_host"]=Config.get('rabbitmq','host')
    libretime_watch.config["rm_vhost"]=Config.get('rabbitmq','vhost')
    libretime_watch.config["rm_port"]=Config.get('rabbitmq','port')
    libretime_watch.config["rm_user"]=Config.get('rabbitmq','user')
    libretime_watch.config["rm_pass"]=Config.get('rabbitmq','password')
    libretime_watch.config["api_key"]=Config.get('general','api_key')
    libretime_watch.config["web_host"]=Config.get('general','base_url')
    libretime_watch.config["web_port"]=Config.get('general','base_port')
    libretime_watch.config["web_basedir"]=Config.get('general','base_dir')



  except:
    logging.error ("can't open the configfile")  
  return libretime_watch.config
