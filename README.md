# Kaggle-RSNA-Intracranial-Hemorrhage
Submission to Kaggle competition RSNA Intracranial Hemorrhage 2019

This is my project for RSNA Intracranial Hemorrhage Detection competition hosted on Kaggle in 2019.

There were a lot of great notebooks exploring the data, and providing ideas for the baseline models by the time I joined the competition. 
I used this <a href='https://www.kaggle.com/akensert/inceptionv3-prev-resnet50-keras-baseline-model'>notebook InceptionV3 (prev. ResNet50) Keras baseline model</a> by <a href='https://www.kaggle.com/akensert'> akensert </a> as a starting point.
And most of ideas and methods for data cleaning and preparation were taken from <a href='https://www.kaggle.com/jhoward'>Jeremy Howard</a> series of notebooks on FastAI implementation for this competition.

Surprising discovery for me during this competition was the new approach to ensembling: averaging predictions of a single model across epochs, instead of combination of predictions of a few models for the final epoch. I understand the intuition behind it, however didn't expect this would work so well. Since I didn't have enough compute power to build and train complex ensemble, I adopted this approach, and combined it with ensembling of 3 models over limited number of epochs.

Based on cross-validation it gave me a significant boost (vs single model averaging), so I used it for the 2nd stage of the competition. I barely had enough time to finish 4 folds before the deadline, and I had to use smaller images - just 256px. 

Undoubtedly bigger images will boost precision. 
Also I believe that traditional K-fold approach with ensembling of a few models shall be more accurate (and I'm planning on checking this belief when I get access to better hardware).
