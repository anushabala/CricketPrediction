__author__ = 'anushabala'
import lasagne
import theano
from theano import tensor as T
import numpy as np
import datetime
from lasagne import regularization

tensor5 = T.TensorType('floatX', (False,) * 5)

# todo implement masks
# todo add regularization


class Solver(object):

    """
    Solver for models.AverageFrameModel.
    todo: this can probably be generalized to all non-LSTM (and maybe even all LSTM) models,
    since the training function and loss function calls will probably stay the same?
    todo maybe create a generic Solver class instead?
    """
    def __init__(self, model,
                 train_X, train_y, val_X, val_y,
                 output_lr=1e-1, tune_lr=1e-3, lr_decay=0.95,
                 model_type='average',
                 num_epochs=1, batch_size=25, tuning_layers=[],
                 num_classes=4, reg=1e-4):
        """
        Create a new FrameAverageSolver instance
        :param model: Instance of the Model class (or a subclass of it) to train
        :param train_X: Training data (5D numpy array, of size (N,P,C,H,W))
        :param train_y: Labels for training data (1D vector, of size (N,))
        :param val_X: Validation data (5D numpy array, of size (N,P,C,H,W))
        :param val_y: Labels for validation data (1D vector, of size (N,))
        :param output_lr: Learning rate for output layer (fully connected layer)
        :param tune_lr: Learning rate for layers to be tuned
        :param num_epochs: Number of epochs to train for
        :param batch_size: Batch size for training
        :param tuning_layers: Keys of layers to tune
        """
        self.model = model
        self.train_X = train_X
        self.train_y = train_y
        self.val_X = val_X
        self.val_y = val_y
        self.output_lr = output_lr
        self.tuning_lr = tune_lr
        self.lr_decay = lr_decay
        self.num_epochs = num_epochs
        self.batch_size = batch_size
        self.num_classes = num_classes
        self.reg = reg
        self.model_type = model_type
        self.tuning_layers = tuning_layers
        self.train_loss_history = []
        self.train_acc_history = []
        self.val_acc_history = []

        if self.model_type == 'late':
            self.tuning_layers = model.tuning_layers
            self.tuning_lr = self.output_lr
            print "Training layers: ", self.tuning_layers
            print "Learning rate", self.tuning_lr

        self._init_train_fn()
        self._init_test_fn()

    def _init_train_fn(self):
        """
        Initialize Theano function to compute loss and update weights using Adam for a single epoch and minibatch.
        """
        input_var = tensor5('input')
        output_var = T.lvector('output')
        one_hot = T.extra_ops.to_one_hot(output_var, self.num_classes, dtype='int64')

        # output_one_hot = T.extra_ops.to_one_hot(output_var, self.num_classes, dtype='int64')
        # Compute losses by iterating over the input variable (a 5D tensor where each "row" represents a clip that
        # has some number of frames.
        [losses, predictions], updates = theano.scan(fn=lambda X_clip, output: self.model.clip_loss(X_clip, output),
                                                     outputs_info=None,
                                                     sequences=[input_var, one_hot])

        loss = losses.mean()

        output_layer = self.model.layer('fc8')
        l2_penalty = regularization.regularize_layer_params(output_layer, regularization.l2) * self.reg * 0.5
        for layer_key in self.tuning_layers:
            layer = self.model.layer(layer_key)
            l2_penalty += regularization.regularize_layer_params(layer, regularization.l2) * self.reg * 0.5
        loss += l2_penalty

        # Get params for output layer and update using Adam
        params = output_layer.get_params(trainable=True)
        adam_update = lasagne.updates.adam(loss, params, learning_rate=self.output_lr)

        # Combine update expressions returned by theano.scan() with update expressions returned from the adam update
        updates.update(adam_update)
        for layer_key in self.tuning_layers:
            layer = self.model.layer(layer_key)
            layer_params = layer.get_params(trainable=True)
            layer_adam_updates = lasagne.updates.adam(loss, layer_params, learning_rate=self.tuning_lr)
            updates.update(layer_adam_updates)
        self.train_function = theano.function([input_var, output_var], [loss, predictions], updates=updates)

    def _init_test_fn(self):
        input_var = tensor5('input')
        output_var = T.lvector('output')
        one_hot = T.extra_ops.to_one_hot(output_var, self.num_classes, dtype='int64')

        # output_one_hot = T.extra_ops.to_one_hot(output_var, self.num_classes, dtype='int64')
        # Compute losses by iterating over the input variable (a 5D tensor where each "row" represents a clip that
        # has some number of frames.
        [losses, predictions, scores], updates = theano.scan(fn=lambda X_clip, output: self.model.clip_loss(X_clip, output, mode='test'),
                                                     outputs_info=None,
                                                     sequences=[input_var, one_hot])
        loss = losses.mean()

        self.test_function = theano.function([input_var, output_var], [loss, predictions, scores], updates=updates)

    def train(self):
        """
        Train the model for num_epochs with batches of size batch_size
        :return:
        """

        iters = 0
        # compute initial validation loss and accuracy
        val_X, val_y = self._get_val_data()
        val_loss, val_predictions, scores = self.test_function(val_X, val_y)
        val_acc = self._compute_accuracy(val_predictions, self.val_y)
        print "Initial validation loss: %f\tValidation accuracy:%2.2f" % (val_loss, val_acc)
        self.val_acc_history.append((0, val_acc))

        print "Started model training"
        start = datetime.datetime.now()

        for i in xrange(self.num_epochs):
            loss = 0
            acc = 0
            for X_batch, y_batch in self.iterate_minibatches():
                iters += 1

                if iters == 1:
                    initial_loss, initial_predictions, scores = self.test_function(X_batch,y_batch)
                    print "(%d/%d) Initial training loss: %f\tTraining accuracy:%2.2f" % (i, self.num_epochs, initial_loss, acc)
                    initial_acc = self._compute_accuracy(initial_predictions, y_batch)
                    self.train_loss_history.append((0, initial_loss))
                    self.train_acc_history.append((0, initial_acc))

                loss, predictions = self.train_function(X_batch, y_batch)
                acc = self._compute_accuracy(predictions, y_batch)

            print "(%d/%d) Training loss: %f\tTraining accuracy:%2.2f" % (i+1, self.num_epochs, loss, acc)
            self.train_loss_history.append((i+1, loss))
            self.train_acc_history.append((i+1, acc))

            if 0 < self.lr_decay < 1:
                self.output_lr *= self.lr_decay
                self.tuning_lr *= self.lr_decay

            if i % 5 == 0:
                val_X, val_y = self._get_val_data()
                val_loss, val_predictions, scores = self.test_function(val_X, val_y)
                val_acc = self._compute_accuracy(val_predictions, self.val_y)
                print "\tValidation loss: %f\tValidation accuracy:%2.2f" % (val_loss, val_acc)
                self.val_acc_history.append((i+1, val_acc))

        end = datetime.datetime.now()
        print "Training took %d seconds" % (end-start).seconds
        if (self.num_epochs - 1) % 5 != 0:
            val_X, val_y = self._get_val_data()
            val_loss, val_predictions, scores = self.test_function(val_X, val_y)
            val_acc = self._compute_accuracy(val_predictions, val_y)
            print "Final Validation loss: %f\tTest accuracy:%2.2f" % (val_loss, val_acc)
            self.val_acc_history.append((self.num_epochs, val_acc))

    def _compute_accuracy(self, predicted_y, true_y):
        return np.array(predicted_y == true_y).mean()

    def _get_val_data(self):
        # todo maybe allow subsampling
        if self.model_type == 'average':
            return self.val_X, self.val_y
        elif self.model_type == 'late':
            return np.take(self.val_X, indices=[0, -1], axis=1), self.val_y

    def predict(self, X, y):
        test_X = X
        if self.model_type == 'late':
            test_X = np.take(X, indices=[0, -1], axis=1)

        loss, predictions, prediction_scores = self.test_function(test_X, y)
        acc = self._compute_accuracy(predictions, y)
        print "Accuracy on test set: %2.4f" % acc
        return predictions, prediction_scores

    def iterate_minibatches(self):
        """
        Iterate over minibatches in one epoch
        :return a single batch of the training data
        """
        num_train = self.train_X.shape[0]
        num_iterations_per_epoch = num_train/self.batch_size
        indexes = np.arange(num_train)
        for i in xrange(num_iterations_per_epoch):
            mask = np.random.choice(indexes, self.batch_size)
            if self.model_type == 'average':
                yield self.train_X[mask], self.train_y[mask]
            elif self.model_type == 'late':
                yield np.take(self.train_X[mask], indices=[0,-1], axis=1), self.train_y[mask]
