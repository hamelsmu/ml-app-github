import numpy as np
import matplotlib.pyplot as plt

from sklearn import svm, datasets
from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix
from sklearn.utils.multiclass import unique_labels


def plot_confusion_matrix(y_true, y_pred, classes,
                          normalize=False,
                          title=None,
                          cmap=plt.cm.Blues):
    """
    This function prints and plots the confusion matrix.
    Normalization can be applied by setting `normalize=True`.
    """
    if not title:
        if normalize:
            title = 'Normalized confusion matrix'
        else:
            title = 'Confusion matrix, without normalization'

    # Compute confusion matrix
    cm = confusion_matrix(y_true, y_pred)
    # Only use the labels that appear in the data
    classes = classes[unique_labels(y_true, y_pred)]
    if normalize:
        cm = cm.astype('float') / cm.sum(axis=1)[:, np.newaxis]
        print("Normalized confusion matrix")
    else:
        print('Confusion matrix, without normalization')

    print(cm)

    fig, ax = plt.subplots()
    im = ax.imshow(cm, interpolation='nearest', cmap=cmap)
    ax.figure.colorbar(im, ax=ax)
    # We want to show all ticks...
    ax.set(xticks=np.arange(cm.shape[1]),
           yticks=np.arange(cm.shape[0]),
           # ... and label them with the respective list entries
           xticklabels=classes, yticklabels=classes,
           title=title,
           ylabel='True label',
           xlabel='Predicted label')

    # Rotate the tick labels and set their alignment.
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right",
             rotation_mode="anchor")

    # Loop over data dimensions and create text annotations.
    fmt = '.2f' if normalize else 'd'
    thresh = cm.max() / 2.
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(j, i, format(cm[i, j], fmt),
                    ha="center", va="center",
                    color="white" if cm[i, j] > thresh else "black")
    fig.tight_layout()
    return ax


class IssueLabeler:
    def __init__(self, 
                 body_text_preprocessor, 
                 title_text_preprocessor, 
                 model, 
                 class_names=['bug', 'feature', 'question']):
        """
        Parameters
        ----------
        body_text_preprocessor: ktext.preprocess.processor
            the text preprocessor trained on issue bodies
        title_text_preprocessor: ktext.preprocess.processor
            text preprocessor trained on issue titles
        model: tensorflow.keras.models
            a keras model that takes as input two tensors: vectorized 
            issue body and issue title.
        class_names: list
            class names as they correspond to the integer indices supplied to the model. 
        """
        self.body_pp = body_text_preprocessor
        self.title_pp = title_text_preprocessor
        self.model = model
        self.class_names = class_names
        
    
    def get_probabilities(self, body:str, title:str):
        """
        Get probabilities for the each class. 
        
        Parameters
        ----------
        body: str
           the issue body
        title: str
            the issue title
            
        Returns
        ------
        Dict[str:float]
        
        Example
        -------
        >>> issue_labeler = IssueLabeler(body_pp, title_pp, model)
        >>> issue_labeler.get_probabilities('hello world', 'hello world')
        {'bug': 0.08372017741203308,
         'feature': 0.6401631832122803,
         'question': 0.2761166989803314}
        """
        #transform raw text into array of ints
        vec_body = self.body_pp.transform([body])
        vec_title = self.title_pp.transform([title])
        
        # get predictions
        probs = self.model.predict(x=[vec_body, vec_title]).tolist()[0]
        
        return {k:v for k,v in zip(self.class_names, probs)}