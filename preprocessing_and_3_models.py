import numpy as np
import pandas as pd
import sys
import os

from math import ceil, floor, log
import cv2

import keras
from keras import backend as K

from imgaug import augmenters as iaa
import pydicom
from pydicom.dataset import Dataset as DcmDataset

import pickle

from efficientnet.keras import EfficientNetB0, EfficientNetB2 
from keras_applications.inception_v3 import InceptionV3

from sklearn.model_selection import ShuffleSplit, train_test_split

test_images_dir = 'stage_2_test_images/'
train_images_dir = 'stage_2_train_images/'

train_1 = "stage_1_train.csv"
train_2 = "stage_2_train.csv"

test1_labels  = 'stage_1_sample_submission.csv'
test2_labels  = 'stage_2_sample_submission.csv'

# define  if algorithm is called for stage1 datasets or stage2
stage2 = True

#preprocessing functions, correcting metadata and rescaling pixel values, mapping to RGB
#---------------------------------------------------------------------------------------------
def correct_dcm(dcm):
    x = dcm.pixel_array + 1000
    px_mode = 4096
    x[x>=px_mode] = x[x>=px_mode] - px_mode
    dcm.PixelData = x.tobytes()
    dcm.RescaleIntercept = -1000

def window_image(dcm, window_center, window_width):
    if (dcm.BitsStored == 12) and (dcm.PixelRepresentation == 0) and (int(dcm.RescaleIntercept) > -100):
        correct_dcm(dcm)
    img = dcm.pixel_array * dcm.RescaleSlope + dcm.RescaleIntercept
    img_min = window_center - window_width // 2
    img_max = window_center + window_width // 2
    img = np.clip(img, img_min, img_max)
    return img

def bsb_window(dcm):
    brain_img = window_image(dcm, 40, 80)
    subdural_img = window_image(dcm, 80, 200)
    soft_img = window_image(dcm, 40, 380)
    
    brain_img = (brain_img - 0) / 80
    subdural_img = (subdural_img - (-20)) / 200
    soft_img = (soft_img - (-150)) / 380
    bsb_img = np.array([brain_img, subdural_img, soft_img]).transpose(1,2,0)
    return bsb_img

# function to check if there is data in the image reflecting brain tissue
#---------------------------------------------------------------------------------------------
def brain_in_window(dcm:DcmDataset, window_center = 40 , window_width = 80):
    "% of pixels in the window reflecting brain matter "

    if (dcm.BitsStored == 12) and (dcm.PixelRepresentation == 0) and (int(dcm.RescaleIntercept) > -100):
        correct_dcm(dcm)

    px = dcm.pixel_array * dcm.RescaleSlope + dcm.RescaleIntercept

    return ((px > window_center-window_width//2) & (px < window_center+window_width//2)).mean().item()
#---------------------------------------------------------------------------------------------

# Image Augmentation
sometimes = lambda aug: iaa.Sometimes(0.35, aug)
augmentation = iaa.Sequential([  
                                iaa.Fliplr(0.5),
                                sometimes(iaa.Crop(px=(20, 80), keep_size = True, sample_independently = False)),
                                sometimes(iaa.Affine(rotate=(-35, 35)))
                            ], random_order = True)       
   
#---------------------------------------------------------------------------------------------
def _read(path, desired_size, toAugment = True):
    """Will be used in DataGenerator"""
    
    dcm = pydicom.dcmread(path)
    
    try:
        img = bsb_window(dcm)
    except:
        img = np.zeros(desired_size)
    
    if toAugment: 
        img = augmentation.augment_image(img)
        
    if desired_size[0] != img.shape[0]:
        img = cv2.resize(img, desired_size[:2], interpolation=cv2.INTER_LINEAR)

    return img


#---------------------------------------------------------------------------------------------
class DataGenerator(keras.utils.Sequence):

    def __init__(self, list_IDs, labels=None, batch_size=1, img_size=(512, 512, 1), 
                 img_dir=train_images_dir, testAugment = False,
                 *args, **kwargs):

        self.list_IDs = list_IDs
        self.labels = labels
        self.batch_size = batch_size
        self.img_size = img_size
        self.img_dir = img_dir
        self.testAugment = testAugment
        self.on_epoch_end()

    def __len__(self):
        return int(ceil(len(self.indices) / self.batch_size))

    def __getitem__(self, index):
        indices = self.indices[index*self.batch_size:(index+1)*self.batch_size]
        list_IDs_temp = [self.list_IDs[k] for k in indices]
        
        if self.labels is not None:
            X, Y = self.__data_generation(list_IDs_temp)
            return X, Y
        else:
            X = self.__data_generation(list_IDs_temp)
            return X
        
    def on_epoch_end(self):
        
        if self.labels is not None: # for training phase we undersample and shuffle
            # keep probability of any=0 and any=1
            keep_prob = self.labels.iloc[:, 0].map({0: 0.35, 1: 0.5})
            keep = (keep_prob > np.random.rand(len(keep_prob)))
            self.indices = np.arange(len(self.list_IDs))[keep]
            np.random.shuffle(self.indices)
        else:
            self.indices = np.arange(len(self.list_IDs))

    def __data_generation(self, list_IDs_temp):
        X = np.empty((self.batch_size, *self.img_size))
        
        if self.labels is not None: # training phase
            Y = np.empty((self.batch_size, 6), dtype=np.float32)
        
            for i, ID in enumerate(list_IDs_temp):
                X[i,] = _read(self.img_dir+ID+".dcm", self.img_size, toAugment = True)
                Y[i,] = self.labels.loc[ID].values
        
            return X, Y

        elif self.testAugment: # test phase with Augmentation
            for i, ID in enumerate(list_IDs_temp):
                X[i,] = _read(self.img_dir+ID+".dcm", self.img_size, toAugment = True)
            return X

        else: # test phase no Augmentation
            for i, ID in enumerate(list_IDs_temp):
                X[i,] = _read(self.img_dir+ID+".dcm", self.img_size, toAugment = False)
            return X

#---------------------------------------------------------------------------------------------
def weighted_log_loss(y_true, y_pred):
    """
    Can be used as the loss function in model.compile()
    ---------------------------------------------------
    """
    class_weights = np.array([2., 1., 1., 1., 1., 1.])
    eps = K.epsilon()
    y_pred = K.clip(y_pred, eps, 1.0-eps)
    out = -(         y_true  * K.log(      y_pred) * class_weights
            + (1.0 - y_true) * K.log(1.0 - y_pred) * class_weights)
    return K.mean(out, axis=-1)

def _normalized_weighted_average(arr, weights=None):
    """
    A simple Keras implementation that mimics that of 
    numpy.average(), specifically for this competition
    """
    if weights is not None:
        scl = K.sum(weights)
        weights = K.expand_dims(weights, axis=1)
        return K.sum(K.dot(arr, weights), axis=1) / scl
    return K.mean(arr, axis=1)


def weighted_loss(y_true, y_pred):
    """
    Will be used as the metric in model.compile()
    ---------------------------------------------
    Similar to the custom loss function 'weighted_log_loss()' above
    but with normalized weights, which should be very similar 
    to the official competition metric:
        https://www.kaggle.com/kambarakun/lb-probe-weights-n-of-positives-scoring
    and hence:
        sklearn.metrics.log_loss with sample weights
    """
    class_weights = K.variable([2., 1., 1., 1., 1., 1.])
    eps = K.epsilon()
    y_pred = K.clip(y_pred, eps, 1.0-eps)
    loss = -(        y_true  * K.log(      y_pred)
            + (1.0 - y_true) * K.log(1.0 - y_pred))
    loss_samples = _normalized_weighted_average(loss, class_weights)
    return K.mean(loss_samples)


def weighted_log_loss_metric(trues, preds):
    """
    Will be used to calculate the log loss 
    of the validation set in PredictionCheckpoint()
    ------------------------------------------
    """
    class_weights = [2., 1., 1., 1., 1., 1.]
    epsilon = 1e-7
    preds = np.clip(preds, epsilon, 1-epsilon)
    loss = trues * np.log(preds) + (1 - trues) * np.log(1 - preds)
    loss_samples = np.average(loss, axis=1, weights=class_weights)
    return - loss_samples.mean()

#---------------------------------------------------------------------------------------------
class PredictionCheckpoint(keras.callbacks.Callback):
    
    def __init__(self, test_df, valid_df, 
                 test_images_dir=test_images_dir, 
                 valid_images_dir=train_images_dir, 
                 batch_size=32, input_size=(224, 224, 3)):
        
        self.test_df = test_df
        self.valid_df = valid_df
        self.test_images_dir = test_images_dir
        self.valid_images_dir = valid_images_dir
        self.batch_size = batch_size
        self.input_size = input_size
        
    def on_train_begin(self, logs={}):
        self.test_predictions = []
        self.valid_predictions = []
        
    def on_epoch_end(self,epoch, logs={}):
        print('End of Epoch #{}'.format(epoch+1))
        if epoch >=3:
# 'direct' prediction
            self.test_predictions.append(
                self.model.predict_generator(
                    DataGenerator(self.test_df.index, None, self.batch_size, self.input_size, self.test_images_dir, testAugment = False), 
                    use_multiprocessing=False,
                    workers=4,
                    verbose=1)[:len(self.test_df)])
# adding 3 augmented predictions
            for i in range(3):
                self.test_predictions.append(
                    self.model.predict_generator(
                        DataGenerator(self.test_df.index, None, self.batch_size, self.input_size, self.test_images_dir, testAugment = True), 
                        use_multiprocessing=False,
                        workers=4,
                        verbose=1)[:len(self.test_df)])
        else:
            print('Skipped predictions...')
            
# by the way skipped the validation here to save time. doing it separately afterwards afyer the fold ended.
# but certainely should do it here for more control and predictability. Just need more compute power :(
        

#---------------------------------------------------------------------------------------------
class MyDeepModel:
    
    def __init__(self, engine, input_dims, batch_size=5, num_epochs=4, learning_rate=1e-3, 
                 decay_rate=1.0, decay_steps=1, weights="imagenet", verbose=1):
        
        self.engine = engine
        self.input_dims = input_dims
        self.batch_size = batch_size
        self.num_epochs = num_epochs
        self.learning_rate = learning_rate
        self.decay_rate = decay_rate
        self.decay_steps = decay_steps
        self.weights = weights
        self.verbose = verbose
        self._build()

    def _build(self):
           
        engine = self.engine(include_top=False, weights=self.weights, input_shape=self.input_dims,
                             backend = keras.backend, layers = keras.layers,
                             models = keras.models, utils = keras.utils)
        
        x = keras.layers.GlobalAveragePooling2D(name='avg_pool')(engine.output)
        x = keras.layers.Dropout(0.2)(x)
        out = keras.layers.Dense(6, activation="sigmoid", name='dense_output')(x)

        self.model = keras.models.Model(inputs=engine.input, outputs=out)
        self.model.compile(loss="binary_crossentropy", optimizer=keras.optimizers.Adam(), metrics=[weighted_loss])
    
    def fit_and_predict(self, train_df, valid_df, test_df):
        
        # callbacks
        pred_history = PredictionCheckpoint(test_df, valid_df, input_size=self.input_dims)
        scheduler = keras.callbacks.LearningRateScheduler(lambda epoch: self.learning_rate * pow(self.decay_rate, floor(epoch / self.decay_steps)))
        
        self.model.fit_generator(
            DataGenerator(
                train_df.index, 
                train_df, 
                self.batch_size, 
                self.input_dims, 
                train_images_dir
            ),
            epochs=self.num_epochs,
            verbose=self.verbose,
            use_multiprocessing=False,
            workers=4,
            callbacks=[pred_history, scheduler]
        )
        
        return pred_history
    
    def save(self, path):
        self.model.save_weights(path)
    
    def load(self, path):
        self.model.load_weights(path)


#---------------------------------------------------------------------------------------------
def read_testset(stage2):

    if stage2 :
        df = pd.read_csv(test2_labels)
    else:
        df = pd.read_csv(test1_labels)

    df["Image"] = df["ID"].str.slice(stop=12)
    df["Diagnosis"] = df["ID"].str.slice(start=13)
    
    df = df.loc[:, ["Label", "Diagnosis", "Image"]]
    df = df.set_index(['Image', 'Diagnosis']).unstack(level=-1)
    
    return df

#---------------------------------------------------------------------------------------------
def read_trainset(Stage2):

    if Stage2:
        df = pd.read_csv(train_2)
    else:
        df = pd.read_csv(train_1)

    duplicates_to_remove = df[df.duplicated('ID', keep = 'first')].index.tolist()        
    
    df["Image"] = df["ID"].str.slice(stop=12)
    df["Diagnosis"] = df["ID"].str.slice(start=13)
    
    df = df.drop(index=duplicates_to_remove)
    df = df.reset_index(drop=True)
    
    df = df.loc[:, ["Label", "Diagnosis", "Image"]]
    df = df.set_index(['Image', 'Diagnosis']).unstack(level=-1)
    
    return df
#---------------------------------------------------------------------------------------------

#drop some images with the brain tissue less than threshold
def discard_no_brain():
    all_labels = df.index.tolist()
        
    pcts = []
    bad_files = []
    for n_file in all_labels:
        dicom = pydicom.dcmread(train_images_dir + n_file + '.dcm')
        try:
            pct = brain_in_window(dicom, 40, 80)
        except:
            pct = -1
            print(n_file,'\n')
            bad_files.append(n_file)
        pcts.append(pct)
           
    useful = pd.DataFrame()
    useful['label'] = all_labels
    useful['pct'] = pcts
    uf = useful.loc[useful.pct > 0.02]
    uf.to_csv('with_brain.csv')

test_df = read_testset(stage2)
df = read_trainset(stage2)

discard_no_brain()
uf = pd.read_csv('with_brain.csv')
df_with_brain = df.loc[uf.label]

BS = 24
# -------------------------------------------------------------------------------------------------------------------------
model_EfficientNetB2 = MyDeepModel(engine=EfficientNetB2, input_dims=(256, 256, 3), batch_size=BS, learning_rate=5e-4,
                    num_epochs=5, decay_rate=0.8, decay_steps=1, weights="imagenet", verbose=1)
model_InceptionV3 = MyDeepModel(engine=InceptionV3, input_dims=(256, 256, 3), batch_size=32, learning_rate=5e-4,
                    num_epochs=5, decay_rate=0.8, decay_steps=1, weights="imagenet", verbose=1)
model_EfficientNetB0 = MyDeepModel(engine=EfficientNetB0, input_dims=(256, 256, 3), batch_size=32, learning_rate=5e-4,
                    num_epochs=5, decay_rate=0.8, decay_steps=1, weights="imagenet", verbose=1)
    
# -------------------------------------------------------------------------------------------------------------------------
# service function to call fit on already intitialized model, with different params if needed
def fit_and_predict_wrap(model, train_df, valid_df, test_df, batch_size = 32, num_epochs = 5, verbose = 1):
    # callbacks
    pred_history = PredictionCheckpoint(test_df, valid_df, input_size = model.input_dims)
    scheduler = keras.callbacks.LearningRateScheduler(lambda epoch: model.learning_rate * pow(model.decay_rate, floor(epoch / model.decay_steps)))
    
    model.model.fit_generator(
        DataGenerator(
            train_df.index, 
            train_df, 
            batch_size, 
            model.input_dims, 
            train_images_dir
        ),
        epochs = num_epochs,
        verbose = verbose,
        use_multiprocessing=False,
        workers=4,
        callbacks=[pred_history, scheduler]
    )
    return pred_history  
  
# -------------------------------------------------------------------------------------------------------------------------

def fit_predict_save(model_T, model_name,fold_num):
    history = model_T.fit_and_predict(df_new.iloc[train_idx], df_new.iloc[valid_idx], test_df)
    history_new.append(history.test_predictions)
    model_T.save((f'St2-{model_name}-5epochs-{fold_num}_fold-4tta-256.h5'))
    with open((f'history_{fold_num}folds.pickle'), 'wb') as f:
        pickle.dump(history_new, f, pickle.HIGHEST_PROTOCOL)
# -------------------------------------------------------------------------------------------------------------------------
# validate the result the same way test set prediction
def valid_predictions(model_T):
    preds = []
    preds.append(model_T.model.predict_generator(
                    DataGenerator(X_val.index, None, 16, (256, 256, 3), train_images_dir, testAugment = False), 
                            use_multiprocessing=False,
                            workers=8,
                            verbose=1)[:len(X_val)])
    for i in range(3):
        preds.append(model_T.model.predict_generator(
                    DataGenerator(X_val.index, None, 16, (256, 256, 3), train_images_dir, testAugment = True), 
                            use_multiprocessing=False,
                            workers=8,
                            verbose=1)[:len(X_val)])
    val = np.average(preds, axis=0)
    return val
# -------------------------------------------------------------------------------------------------------------------------

    
X_train, X_val = train_test_split(df_with_brain, test_size=0.1, random_state=1970)
df_new = X_train.copy()

ss_new = ShuffleSplit(n_splits=4, test_size=0.2, random_state=1970).split(df_new.index)
history_new = []
valid_history = []

num_folds = 5
for cnt in range(1,num_folds+1):
    print('* * * * * * * * * * * * * * * * ')
    print(f'* * * Fold * * * #{cnt}')
    train_idx, valid_idx = next(ss_new)

    fit_predict_save(model_EfficientNetB2, 'B2', cnt)
    fit_predict_save(model_InceptionV3, 'V3', cnt)
    fit_predict_save(model_EfficientNetB0, 'B0', cnt)
    
    V_B0 = valid_predictions(model_EfficientNetB0)
    val_B0 = weighted_log_loss_metric(X_val.values, V_B0)
    V_B2 = valid_predictions(model_EfficientNetB2)
    val_B2 = weighted_log_loss_metric(X_val.values, V_B2)
    V_V3 = valid_predictions(model_InceptionV3)
    val_V3 = weighted_log_loss_metric(X_val.values, V_V3)
    
    all3_val = [V_B0 , V_B2 , V_V3]
    val_avg3 = np.average(all3_val, axis=0)
    val_ALL3 = weighted_log_loss_metric(X_val.values, val_avg3)
    
    valid_history.append({'B0':val_B0})
    valid_history.append({'B2':val_B2})
    valid_history.append({'V3':val_V3})
    valid_history.append({'avg_3':val_ALL3})
    with open('history_validation.pickle', 'wb') as f:
        pickle.dump(valid_history, f, pickle.HIGHEST_PROTOCOL)

    
shapes = history_new[0][0].shape

folds_preds = np.zeros((len(history_new),shapes[0],shapes[1]))
for i in range(len(history_new)):
    folds_preds[i] = np.average(history_new[i], axis=0)

averaged_preds = np.average(folds_preds, axis=0)

test_df_pred = test_df.copy()
test_df_pred.iloc[:, :] = averaged_preds

test_df_pred = test_df_pred.stack().reset_index()
test_df_pred.insert(loc=0, column='ID', value=test_df_pred['Image'].astype(str) + "_" + test_df_pred['Diagnosis'])
test_df_pred = test_df_pred.drop(["Image", "Diagnosis"], axis=1)
test_df_pred.to_csv('stage2_b2v3b0_5e-4folds-2_last_epochs-4TTA-256.csv', index=False)
