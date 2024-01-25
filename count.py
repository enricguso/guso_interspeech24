from datetime import datetime

import os
from os.path import join as pjoin
print(' ')
already = os.listdir(pjoin(pjoin('/home/ubuntu/dataset', 'train'),'recsourcedirectivity_right'))
already += os.listdir(pjoin(pjoin('/home/ubuntu/dataset', 'val'), 'recsourcedirectivity_right'))
already += os.listdir(pjoin(pjoin('/home/ubuntu/dataset', 'test'), 'recsourcedirectivity_right'))
current_time = datetime.now()
formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")

print(str(len(already))+' files already processed at   '+formatted_time)
print(' ')
#import os
#from os.path import join as pjoin



