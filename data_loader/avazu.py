import shutil
import struct
from collections import defaultdict
from pathlib import Path

import lmdb
import numpy as np
import torch.utils.data
from tqdm import tqdm
import pandas as pd

#  class GowallaDataset(torch.utils.data.Dataset)
#      """
#      Gowalla dataset
#      """
#      #  def __init__(self, dataset_path=None, cache_path='.avazu', rebuild_cache=False, min_threshold=10, num_blocks=8):
#      def __init__(self,dataset_path="../data/gowalla"):
#          # train or test
#          cprint(f'loading [{path}]')
#          self.split = config['A_split']
#          self.folds = config['A_n_fold']
#          self.mode_dict = {'train': 0, "test": 1}
#          self.mode = self.mode_dict['train']
#          self.n_user = 0
#          self.m_item = 0
#          train_file = path + '/train.txt'
#          test_file = path + '/test.txt'
#          self.path = path
#          trainUniqueUsers, trainItem, trainUser = [], [], []
#          testUniqueUsers, testItem, testUser = [], [], []
#          self.traindataSize = 0
#          self.testDataSize = 0

#          with open(train_file) as f:
#              for l in f.readlines():
#                  if len(l) > 0:
#                      l = l.strip('\n').split(' ')
#                      items = [int(i) for i in l[1:]]
#                      uid = int(l[0])
#                      trainUniqueUsers.append(uid)
#                      trainUser.extend([uid] * len(items))
#                      trainItem.extend(items)
#                      self.m_item = max(self.m_item, max(items))
#                      self.n_user = max(self.n_user, uid)
#                      self.traindataSize += len(items)
#          self.trainUniqueUsers = np.array(trainUniqueUsers)
#          self.trainUser = np.array(trainUser)
#          self.trainItem = np.array(trainItem)

#          with open(test_file) as f:
#              for l in f.readlines():
#                  if len(l) > 0:
#                      l = l.strip('\n').split(' ')
#                      items = [int(i) for i in l[1:]]
#                      uid = int(l[0])
#                      testUniqueUsers.append(uid)
#                      testUser.extend([uid] * len(items))
#                      testItem.extend(items)
#                      self.m_item = max(self.m_item, max(items))
#                      self.n_user = max(self.n_user, uid)
#                      self.testDataSize += len(items)
#          self.m_item += 1
#          self.n_user += 1
#          self.testUniqueUsers = np.array(testUniqueUsers)
#          self.testUser = np.array(testUser)
#          self.testItem = np.array(testItem)
        
#          self.Graph = None
#          print(f"{self.trainDataSize} interactions for training")
#          print(f"{self.testDataSize} interactions for testing")
#          print(f"{world.dataset} Sparsity : {(self.trainDataSize + self.testDataSize) / self.n_users / self.m_items}")

#          # (users,items), bipartite graph
#          self.UserItemNet = csr_matrix((np.ones(len(self.trainUser)), (self.trainUser, self.trainItem)),
#                                        shape=(self.n_user, self.m_item))
#          self.users_D = np.array(self.UserItemNet.sum(axis=1)).squeeze()
#          self.users_D[self.users_D == 0.] = 1
#          self.items_D = np.array(self.UserItemNet.sum(axis=0)).squeeze()
#          self.items_D[self.items_D == 0.] = 1.
#          # pre-calculate
#          self._allPos = self.getUserPosItems(list(range(self.n_user)))
#          self.__testDict = self.__build_test()
#          print(f"{world.dataset} is ready to go")


class AvazuDataset(torch.utils.data.Dataset):
    """
    Avazu Click-Through Rate Prediction Dataset

    Dataset preparation
        Remove the infrequent features (appearing in less than threshold instances) and treat them as a single feature

    :param dataset_path: avazu train path
    :param cache_path: lmdb cache path
    :param rebuild_cache: If True, lmdb cache is refreshed
    :param min_threshold: infrequent feature threshold

    Reference
        https://www.kaggle.com/c/avazu-ctr-prediction
    """

    def __init__(self, dataset_path=None, cache_path='.avazu', rebuild_cache=False, min_threshold=10, num_blocks=8):
        self.NUM_FEATS = 22
        self.min_threshold = min_threshold
        self.num_blocks = num_blocks
        if rebuild_cache or not Path(cache_path).exists():
            shutil.rmtree(cache_path, ignore_errors=True)
            if dataset_path is None:
                raise ValueError('create cache: failed: dataset_path is None')
            self.__build_cache(dataset_path, cache_path)
        self.env = lmdb.open(cache_path, create=False, lock=False, readonly=True)
        with self.env.begin(write=False) as txn:
            self.length = txn.stat()['entries'] - 1
            self.field_dims = np.frombuffer(txn.get(b'field_dims'), dtype=np.uint32)

    def __getitem__(self, index):
        with self.env.begin(write=False) as txn:
            np_array = np.frombuffer(
                txn.get(struct.pack('>I', index)), dtype=np.uint32).astype(dtype=np.long)
        return np_array[1:], np_array[0]

    def __len__(self):
        return self.length

    def __build_cache(self, path, cache_path):
        feat_mapper, defaults, field_dims = self.__get_feat_mapper(path)
        with lmdb.open(cache_path, map_size=int(1e11)) as env:
            with env.begin(write=True) as txn:
                txn.put(b'field_dims', field_dims.tobytes())
            for buffer in self.__yield_buffer(path, feat_mapper, defaults):
                with env.begin(write=True) as txn:
                    for key, value in buffer:
                        txn.put(key, value)

    def __get_feat_mapper(self, path):
        feat_cnts = defaultdict(lambda: defaultdict(int))
        new_feat_cnts = defaultdict(lambda: defaultdict(int))
        with open(path) as f:
            f.readline()
            pbar = tqdm(f, mininterval=1, smoothing=0.1)
            pbar.set_description('Create avazu dataset cache: counting features')
            for line in pbar:
                values = line.rstrip('\n').split(',')
                if len(values) != self.NUM_FEATS + 2:
                    continue
                for i in range(1, self.NUM_FEATS + 1):
                    feat_cnts[i][values[i + 1]] += 1
        feat_mapper = {i: {feat for feat, c in cnt.items() if c >= self.min_threshold} for i, cnt in feat_cnts.items()}
        feat_mapper = {i: {feat: idx for idx, feat in enumerate(cnt)} for i, cnt in feat_mapper.items()}
        defaults = {i: len(cnt) for i, cnt in feat_mapper.items()}

        for field, sub_dict in feat_cnts.items():
            for key in list(sub_dict.keys()):
                if sub_dict[key] < self.min_threshold:
                    sub_dict['default'] += 1
                else:
                    new_feat_cnts[field][feat_mapper[field][key]] = sub_dict[key]

            if sub_dict['default'] != 0:
                new_feat_cnts[field][len(feat_mapper[field])] = sub_dict['default']
        field_dims = self.__get_field_dims(new_feat_cnts)
        return feat_mapper, defaults, field_dims

    def __yield_buffer(self, path, feat_mapper, defaults, buffer_size=int(1e5)):
        item_idx = 0
        buffer = list()
        with open(path) as f:
            f.readline()
            pbar = tqdm(f, mininterval=1, smoothing=0.1)
            pbar.set_description('Create avazu dataset cache: setup lmdb')
            for line in pbar:
                values = line.rstrip('\n').split(',')
                if len(values) != self.NUM_FEATS + 2:
                    continue
                np_array = np.zeros(self.NUM_FEATS + 1, dtype=np.uint32)
                np_array[0] = int(values[1])
                for i in range(1, self.NUM_FEATS + 1):
                    np_array[i] = feat_mapper[i].get(values[i+1], defaults[i])
                buffer.append((struct.pack('>I', item_idx), np_array.tobytes()))
                item_idx += 1
                if item_idx % buffer_size == 0:
                    yield buffer
                    buffer.clear()
            yield buffer

    def __get_field_dims(self, data):
        all_freq = None
        index_offset = 0
        field_dims = np.zeros(self.NUM_FEATS, dtype=np.uint32)
        for i, col in enumerate(data.keys()):
            freq = pd.Series(data[col]).sort_values(ascending=False)
            freq.index = freq.index + index_offset
            if all_freq is None:
                all_freq = freq
            else:
                all_freq = pd.concat([all_freq, freq], axis=0)
            index_offset += len(freq)
            field_dims[i] = len(freq)

        return field_dims
