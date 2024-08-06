import configparser
import os


def load_config(config_file_path='config.ini'):
    config = configparser.ConfigParser()

    # Check if the config file exists
    if not os.path.exists(config_file_path):
        # Create a default config file
        config['telegram'] = {'bot_token': 'YOUR_TELEGRAM_BOT_TOKEN'}
        config['settings'] = {
            'font_size': '24',
            'font_color': 'white',
            'font_family': 'Arial',
            'opacity': '0.8',
            'scroll_direction': 'right-to-left'
        }
        with open(config_file_path, 'w') as configfile:
            config.write(configfile)
    else:
        config.read(config_file_path)

    return config


# Load configuration once, globally available
config = load_config()


def save_config(config, config_file_path='config.ini'):
    with open(config_file_path, 'w') as configfile:
        config.write(configfile)
