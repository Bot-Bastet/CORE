from setuptools import setup, find_packages

package_name = 'spotbot_streaming'

setup(
    name=package_name,
    version='1.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/streaming.launch.py']),
        ('share/' + package_name + '/config', ['config/streaming.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={
        'console_scripts': [
            'streaming_engine     = spotbot_streaming.streaming_engine:main',
            'wifi_watchdog_node  = spotbot_streaming.wifi_watchdog_node:main',
        ],
    },
)
