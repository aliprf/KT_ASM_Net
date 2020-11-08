from configuration import DatasetName, DatasetType, \
    AffectnetConf, IbugConf, W300Conf, InputDataSize, LearningConfig, CofwConf, WflwConf
from tf_record_utility import TFRecordUtility
from clr_callback import CyclicLR
from cnn_model import CNNModel
from custom_Losses import Custom_losses
from Data_custom_generator import CustomHeatmapGenerator
from PW_Data_custom_generator import PWCustomHeatmapGenerator
import tensorflow as tf
tf.compat.v1.disable_eager_execution()

from tensorflow import keras

print(tf.__version__)
print(keras.__version__)

from tensorflow.keras.callbacks import ModelCheckpoint

from tensorflow.keras.optimizers import Adam
import numpy as np
import matplotlib.pyplot as plt
import math
from tensorflow.keras.callbacks import CSVLogger
from datetime import datetime
from sklearn.utils import shuffle
import os
from sklearn.model_selection import train_test_split
from numpy import save, load, asarray
import os.path
from tensorflow.keras import losses
from keras import backend as K
import csv
from skimage.io import imread


class Train:
    def __init__(self, use_tf_record, dataset_name, custom_loss, arch, inception_mode, num_output_layers,
                 train_on_batch, on_point, heatmap, weight=None, accuracy=100):
        c_loss = Custom_losses()
        self.dataset_name = dataset_name

        if dataset_name == DatasetName.ibug:
            self.SUM_OF_ALL_TRAIN_SAMPLES = IbugConf.orig_number_of_training
            self.output_len = IbugConf.num_of_landmarks * 2
            if accuracy == 100:
                self.tf_train_path = IbugConf.tf_train_path
                self.tf_eval_path = IbugConf.tf_evaluation_path
            elif accuracy == 90:
                self.tf_train_path = IbugConf.tf_train_path_95
                self.tf_eval_path = IbugConf.tf_evaluation_path_95

        elif dataset_name == DatasetName.cofw:
            self.SUM_OF_ALL_TRAIN_SAMPLES = CofwConf.orig_number_of_training
            self.output_len = CofwConf.num_of_landmarks * 2
            if accuracy == 100:
                self.tf_train_path = CofwConf.no_aug_train_tf_path+'train100.tfrecords'
                self.tf_eval_path = CofwConf.no_aug_train_tf_path+'eval100.tfrecords'
            elif accuracy == 90:
                self.tf_train_path = CofwConf.no_aug_train_tf_path+'train90.tfrecords'
                self.tf_eval_path = CofwConf.no_aug_train_tf_path+'eval90.tfrecords'

        elif dataset_name == DatasetName.wflw:
            self.SUM_OF_ALL_TRAIN_SAMPLES = WflwConf.orig_number_of_training
            self.output_len = WflwConf.num_of_landmarks * 2
            if accuracy == 100:
                self.tf_train_path = WflwConf.no_aug_train_tf_path + 'train100.tfrecords'
                self.tf_eval_path = WflwConf.no_aug_train_tf_path + 'eval100.tfrecords'
            elif accuracy == 90:
                self.tf_train_path = WflwConf.no_aug_train_tf_path + 'train90.tfrecords'
                self.tf_eval_path = WflwConf.no_aug_train_tf_path + 'eval90.tfrecords'

        self.BATCH_SIZE = LearningConfig.batch_size
        self.STEPS_PER_VALIDATION_EPOCH = LearningConfig.steps_per_validation_epochs
        self.STEPS_PER_EPOCH = self.SUM_OF_ALL_TRAIN_SAMPLES // self.BATCH_SIZE
        self.EPOCHS = LearningConfig.epochs

        if custom_loss:
            self.loss = c_loss.custom_loss_hm
        else:
            # self.loss = losses.mean_squared_error
            self.loss = losses.mean_absolute_error

        self.arch = arch
        self.inception_mode = inception_mode
        self.weight = weight
        self.num_output_layers = num_output_layers

        self.accuracy = accuracy

        if train_on_batch:
            self.train_fit_on_batch()
        elif heatmap and use_tf_record:
            self.train_fit_heatmap()
        elif use_tf_record:
            self.train_fit()
        else:
            self.train_fit_gen(on_point)

    def train_fit_on_batch(self):
        """"""

        """define optimizers"""
        optimizer = self._get_optimizer()

        '''create asm_pre_trained net'''
        cnn = CNNModel()
        asm_model = cnn.create_multi_branch_mn(inp_shape=[224, 224, 3], num_branches=3)
        # asm_model.load_weights('')
        '''creating model'''
        model = cnn.get_model(None, self.arch, self.num_output_layers,)

        '''loading weights'''
        if self.weight is not None:
            asm_model.load_weights('weights-101-0.00021_asm_model.h5')
            # model.load_weights(self.weight)

        '''compiling model'''
        model.compile(loss=self._generate_loss(),
                      optimizer=optimizer,
                      metrics=['mse', 'mae'],
                      loss_weights=self._generate_loss_weights()
                      )

        '''create train, validation, test data iterator'''
        x_train_filenames, x_val_filenames, y_train_filenames, y_val_filenames = self._create_generators()

        '''save logs'''
        log_file_name = 'log_' + datetime.now().strftime("%Y%m%d-%H%M%S")+".csv"
        metrics_names = model.metrics_names
        metrics_names.append('epoch')
        self.write_loss_log(log_file_name, metrics_names)
        ''''''
        self.STEPS_PER_EPOCH = len(x_train_filenames) // self.BATCH_SIZE

        for epoch in range(self.EPOCHS):
            loss = []
            for batch in range(self.STEPS_PER_EPOCH):
                try:
                    imgs, hm_g = self.get_batch_sample(batch, x_train_filenames, y_train_filenames)
                    # print(imgs.shape)
                    # print(hm_g.shape)

                    hm_predicted = asm_model.predict_on_batch(imgs)
                    loss = model.train_on_batch(imgs, [hm_g, hm_predicted[0], hm_predicted[1], hm_predicted[2]])
                   # print(f'Epoch: {epoch} \t batch:{batch} of {self.STEPS_PER_EPOCH}\t\n  moedl Loss: {loss}')
                except:
                    print('catch')
            loss.append(epoch)
            self.write_loss_log(log_file_name, loss)
            model.save_weights('weight_ep_'+str(epoch)+'_los_'+str(loss)+'.h5')

    def write_loss_log(self, file_name, row_data):
        with open(file_name, 'w') as csvfile:
            filewriter = csv.writer(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            filewriter.writerow(row_data)

    def get_batch_sample(self, batch, x_train_filenames, y_train_filenames):
        img_path = IbugConf.train_images_dir
        tr_path = IbugConf.train_hm_dir

        batch_x = x_train_filenames[batch * self.BATCH_SIZE:(batch + 1) * self.BATCH_SIZE]
        batch_y = y_train_filenames[batch * self.BATCH_SIZE:(batch + 1) * self.BATCH_SIZE]

        img_batch = np.array([imread(img_path + file_name) for file_name in batch_x])
        lbl_batch = np.array([load(tr_path + file_name) for file_name in batch_y])

        return img_batch, lbl_batch
        # return np.zeros([self.BATCH_SIZE, 224, 224, 3]), np.zeros([self.BATCH_SIZE, 56, 56, 68])

    def train_fit_gen(self, on_point):

        """train_fit_gen"""

        '''prepare callbacks'''
        callbacks_list = self._prepare_callback()

        """define optimizers"""
        optimizer = self._get_optimizer()

        '''creating model'''
        cnn = CNNModel()
        model = cnn.get_model(None, self.arch, self.num_output_layers)

        '''loading weights'''
        if self.weight is not None:
            model.load_weights(self.weight)

        '''create train, validation, test data iterator'''
        x_train_filenames, x_val_filenames, y_train_filenames, y_val_filenames = self._create_generators()

        is_single = True
        if self.num_output_layers > 1:
            is_single = False

        if not on_point:
            my_training_batch_generator = CustomHeatmapGenerator(is_single, x_train_filenames, y_train_filenames,
                                                                 self.BATCH_SIZE, self.num_output_layers, self.accuracy)
            my_validation_batch_generator = CustomHeatmapGenerator(is_single, x_val_filenames, y_val_filenames,
                                                                   self.BATCH_SIZE, self.num_output_layers, self.accuracy)
        else:
            my_training_batch_generator = PWCustomHeatmapGenerator(is_single, x_train_filenames, y_train_filenames,
                                                                   self.BATCH_SIZE, self.num_output_layers,
                                                                   self.accuracy)
            my_validation_batch_generator = PWCustomHeatmapGenerator(is_single, x_val_filenames, y_val_filenames,
                                                                     self.BATCH_SIZE, self.num_output_layers,
                                                                     self.accuracy)
        '''compiling model'''
        model.compile(loss=self._generate_loss(),
                      optimizer=optimizer,
                      metrics=['mse', 'mae'],
                      loss_weights=self._generate_loss_weights()
                      )

        '''train Model '''
        print('< ========== Start Training ============= >')
        model.fit_generator(generator=my_training_batch_generator,
                            epochs=self.EPOCHS,
                            verbose=1,
                            validation_data=my_validation_batch_generator,
                            steps_per_epoch=self.STEPS_PER_EPOCH,
                            callbacks=callbacks_list,
                            use_multiprocessing=True,
                            workers=16,
                            max_queue_size=32
                            )

    def train_fit_heatmap(self):
        tf_record_util = TFRecordUtility(self.output_len)

        '''prepare callbacks'''
        callbacks_list = self._prepare_callback()

        ''' define optimizers'''
        optimizer = self._get_optimizer()

        '''create train, validation, test data iterator'''
        train_images, train_landmarks, _, train_heatmaps, _ = tf_record_util.create_training_tensor(
            tfrecord_filename=self.tf_train_path, batch_size=self.BATCH_SIZE)

        validation_images, validation_landmarks, _, validation_heatmaps, _ = tf_record_util.create_training_tensor(
            tfrecord_filename=self.tf_eval_path, batch_size=self.BATCH_SIZE)

        '''creating model'''
        cnn = CNNModel()
        model = cnn.get_model(train_images=train_images, arch=self.arch, num_output_layers=self.num_output_layers,
                              output_len=self.output_len,
                              input_tensor=train_images, inp_shape=None)

        if self.weight is not None:
            model.load_weights(self.weight)

        '''compiling model'''
        model.compile(loss=self._generate_loss(),
                      optimizer=optimizer,
                      metrics=['mse', 'mae'],
                      target_tensors=self._generate_target_tensors(train_heatmaps),
                      loss_weights=self._generate_loss_weights()
                      )

        '''train Model '''
        print('< ========== Start Training ============= >')

        history = model.fit(train_images,
                            train_heatmaps,
                            epochs=self.EPOCHS,
                            steps_per_epoch=self.STEPS_PER_EPOCH,
                            validation_data=(validation_images, validation_heatmaps),
                            validation_steps=self.STEPS_PER_VALIDATION_EPOCH,
                            verbose=1, callbacks=callbacks_list
                            )

    def train_fit(self):
        tf_record_util = TFRecordUtility(self.output_len)

        '''prepare callbacks'''
        callbacks_list = self._prepare_callback()

        ''' define optimizers'''
        optimizer = self._get_optimizer()

        '''create train, validation, test data iterator'''
        train_images, train_landmarks = tf_record_util.create_training_tensor_points(tfrecord_filename=
                                                                                     self.tf_train_path,
                                                                                     batch_size=self.BATCH_SIZE)
        validation_images, validation_landmarks = \
            tf_record_util.create_training_tensor_points(tfrecord_filename=self.tf_eval_path,
                                                         batch_size=self.BATCH_SIZE)

        '''creating model'''
        cnn = CNNModel()
        model = cnn.get_model(train_images=train_images, arch=self.arch, num_output_layers=self.num_output_layers,
                              output_len=self.output_len,
                              input_tensor=train_images, inp_shape=None)

        if self.weight is not None:
            model.load_weights(self.weight)

        '''compiling model'''
        model.compile(loss=self._generate_loss(),
                      optimizer=optimizer,
                      metrics=['mse', 'mae'],
                      target_tensors=self._generate_target_tensors(train_landmarks),
                      loss_weights=self._generate_loss_weights()
                      )

        '''train Model '''
        print('< ========== Start Training ============= >')

        history = model.fit(train_images,
                            train_landmarks,
                            epochs=self.EPOCHS,
                            steps_per_epoch=self.STEPS_PER_EPOCH,
                            validation_data=(validation_images, validation_landmarks),
                            validation_steps=self.STEPS_PER_VALIDATION_EPOCH,
                            verbose=1, callbacks=callbacks_list
                            )

    def _generate_loss(self):
        loss = []
        for i in range(self.num_output_layers):
            loss.append(self.loss)
        return loss

    def _generate_target_tensors(self, target_tensor):
        tensors = []
        for i in range(self.num_output_layers):
            tensors.append(target_tensor)
        return tensors

    def _generate_loss_weights(self):
        wights = [1]
        # wights = [0.7*3*1,  # main labels
        #           0.3*0.5,  # asm 85 labels
        #           0.3*0.7,  # asm 90 labels
        #           0.3*0.9   # asm 97 labels
        #           ]
        # for i in range(self.num_output_layers):
        #     wights.append(1)
        return wights

    def _get_optimizer(self):
        return Adam(lr=1e-2, beta_1=0.9, beta_2=0.999, decay=1e-5, amsgrad=False)

    def _prepare_callback(self):
        early_stop = keras.callbacks.EarlyStopping(monitor='val_loss', min_delta=0, patience=50, verbose=1, mode='min')
        file_path = "ds_"+str(self.dataset_name)+"_ac_" + str(self.accuracy) + "_weights-{epoch:02d}-{loss:.5f}.h5"
        checkpoint = ModelCheckpoint(file_path, monitor='loss', verbose=1, save_best_only=True, mode='min')
        csv_logger = CSVLogger('log.csv', append=True, separator=';')

        clr = CyclicLR(
            mode=LearningConfig.CLR_METHOD,
            base_lr=LearningConfig.MIN_LR,
            max_lr=LearningConfig.MAX_LR,
            step_size=LearningConfig.STEP_SIZE * (self.SUM_OF_ALL_TRAIN_SAMPLES // self.BATCH_SIZE))

        logdir = "logs/scalars/" + datetime.now().strftime("%Y%m%d-%H%M%S")
        # tensorboard_callback = keras.callbacks.TensorBoard(log_dir=logdir)
        # return [checkpoint, early_stop, csv_logger, clr, tensorboard_callback]
        return [checkpoint, early_stop, csv_logger, clr]


    def _create_generators(self):
        tf_utils = TFRecordUtility()

        # if os.path.isfile('x_train_filenames.npy') and \
        #         os.path.isfile('x_val_filenames.npy') and \
        #         os.path.isfile('y_train_filenames.npy') and \
        #         os.path.isfile('y_val_filenames.npy'):
        #     x_train_filenames = load('x_train_filenames.npy')
        #     x_val_filenames = load('x_val_filenames.npy')
        #     y_train = load('y_train_filenames.npy')
        #     y_val = load('y_val_filenames.npy')
        # else:
        filenames, labels = tf_utils.create_image_and_labels_name()

        filenames_shuffled, y_labels_shuffled = shuffle(filenames, labels)

        x_train_filenames, x_val_filenames, y_train, y_val = train_test_split(
            filenames_shuffled, y_labels_shuffled, test_size=0.1, random_state=1)

        save('x_train_filenames.npy', x_train_filenames)
        save('x_val_filenames.npy', x_val_filenames)
        save('y_train_filenames.npy', y_train)
        save('y_val_filenames.npy', y_val)

        return x_train_filenames, x_val_filenames, y_train, y_val

    def _save_model_with_weight(self, model):
        model_json = model.to_json()

        with open("model_asm_shrink.json", "w") as json_file:
            json_file.write(model_json)

        model.save_weights("model_asm_shrink.h5")
        print("Saved model to disk")
