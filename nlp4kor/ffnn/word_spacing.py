import gzip
import os
import sys
import time
import traceback

import numpy as np
import tensorflow as tf

from bage_utils.datafile_util import DataFileUtil
from bage_utils.dataset import DataSet
from bage_utils.file_util import FileUtil
from bage_utils.num_util import NumUtil
from bage_utils.one_hot_vector import OneHotVector
from bage_utils.watch_util import WatchUtil
from nlp4kor.config import log, KO_WIKIPEDIA_ORG_DIR, KO_WIKIPEDIA_ORG_CHARACTERS_FILE, \
    KO_WIKIPEDIA_ORG_WORD_SPACING_MODEL_DIR, KO_WIKIPEDIA_ORG_TRAIN_SENTENCES_FILE, KO_WIKIPEDIA_ORG_TEST_SENTENCES_FILE, KO_WIKIPEDIA_ORG_VALID_SENTENCES_FILE, \
    KO_WIKIPEDIA_ORG_SENTENCES_FILE


class WordSpacing(object):
    graph_nodes = {}

    @classmethod
    def learning(cls, n_train, n_valid, n_test, batch_size, left_gram, right_gram, model_file, features_vector, labels_vector, n_hidden1=100,
                 learning_rate=0.01):
        ngram = left_gram + right_gram
        n_features = len(features_vector) * ngram  # number of features = 17,380 * 4
        n_classes = len(labels_vector) if len(labels_vector) >= 3 else 1  # number of classes = 2 but len=1

        log.info('load characters list...')
        log.info('load characters list OK. len: %s\n' % NumUtil.comma_str(len(features_vector)))
        watch = WatchUtil()

        train_file = os.path.join(KO_WIKIPEDIA_ORG_DIR, 'datasets', 'word_spacing',
                                  'ko.wikipedia.org.dataset.left=%d.right=%d.train.gz' % (left_gram, right_gram))
        valid_file = train_file.replace('.train.', '.valid.')
        test_file = train_file.replace('.train.', '.test.')
        if True:  # FIXME: TEST not os.path.exists(train_file) or not os.path.exists(valid_file) or not os.path.exists(test_file):
            dataset_dir = os.path.dirname(train_file)
            if not os.path.exists(dataset_dir):
                os.makedirs(dataset_dir)

            watch.start('create dataset')
            log.info('create dataset...')

            for name, data_file, dataset_file, to_one_hot_vector in (('valid', KO_WIKIPEDIA_ORG_VALID_SENTENCES_FILE, valid_file, False),
                                                                     ('test', KO_WIKIPEDIA_ORG_TEST_SENTENCES_FILE, test_file, False),
                                                                     ('train', KO_WIKIPEDIA_ORG_TRAIN_SENTENCES_FILE, train_file, False),
                                                                     ):
                max_train_sentences = FileUtil.count_lines(data_file, gzip_format=True)
                features, labels = [], []
                check_interval = 10000
                log.info('total: %s' % NumUtil.comma_str(max_train_sentences))

                with gzip.open(data_file, 'rt') as f:
                    for i, line in enumerate(f, 1):
                        # if name == 'train' and max_train_sentences < i:
                        #     break

                        if i % check_interval == 0:
                            time.sleep(0.1)  # prevent cpu overload
                            log.info('create dataset... %.1f%% readed. data len: %s' % (i / max_train_sentences * 100, NumUtil.comma_str(len(features))))

                        _f, _l = WordSpacing.sentence2features_labels(line.strip(), left_gram=left_gram, right_gram=right_gram)
                        features.extend(_f)
                        labels.extend(_l)

                    dataset = DataSet(features=features, labels=labels, features_vector=features_vector, labels_vector=labels_vector, name=name)
                    log.info('dataset save... %s' % dataset_file)
                    dataset.save(dataset_file, gzip_format=True, verbose=True)
                    log.info('dataset save OK. %s' % dataset_file)
                    log.info('dataset: %s' % dataset)

            log.info('create dataset OK.\n')
            watch.stop('create dataset')

        watch.start('dataset load')
        log.info('dataset load...')
        train = DataSet.load(train_file, gzip_format=True, max_len=n_train, verbose=True)

        valid = DataSet.load(valid_file, gzip_format=True, max_len=n_valid, verbose=True)
        log.info('valid.convert_to_one_hot_vector()...')
        valid = valid.convert_to_one_hot_vector(verbose=True)
        log.info('valid.convert_to_one_hot_vector() OK.')

        test = DataSet.load(test_file, gzip_format=True, max_len=n_test, verbose=True)
        log.info('test.convert_to_one_hot_vector()...')
        test = test.convert_to_one_hot_vector(verbose=True)
        log.info('test.convert_to_one_hot_vector() OK.')

        log.info(train)
        log.info(valid)
        log.info(test)
        log.info('dataset load OK.\n')
        watch.stop('dataset load')

        # log.info('check samples...')
        # for i, (features_batch, labels_batch) in enumerate(train.next_batch(batch_size=5, to_one_hot_vector=True), 1):
        #     if i > 2:
        #         break
        #     for a, b in zip(features_batch, labels_batch):
        #         feature, label = a, b
        #         _feature = feature.reshape((ngram, len(features_vector)))
        #         chars = ''.join(features_vector.to_values(_feature))
        #         has_space = np.argmax(label)
        #         log.info('[%s] %s -> %s, %s (len=%s) %s (len=%s)' % (i, chars, has_space, feature, len(feature), label, len(label)))
        # log.info('check samples OK.\n')

        graph = WordSpacing.build_FFNN(n_features, n_classes, n_hidden1, learning_rate, watch)

        train_step, X, Y, cost, predicted, accuracy = graph['train_step'], graph['X'], graph['Y'], graph['cost'], graph['predicted'], graph['accuracy']

        with tf.Session() as sess:
            sess.run(tf.global_variables_initializer())

            n_input = 0
            log.info('total: %s' % NumUtil.comma_str(train.size))
            log.info('learn...')
            watch.start('learn')
            for step, (features_batch, labels_batch) in enumerate(train.next_batch(batch_size=batch_size), 1):
                n_input += batch_size
                sess.run(train_step, feed_dict={X: features_batch, Y: labels_batch})
                log.info('[%s][%.1f%%] valid cost: %.4f' % (NumUtil.comma_str(n_input), n_input / train.size * 100,
                                                            sess.run(cost, feed_dict={X: valid.features, Y: valid.labels})))
            watch.stop('learn')
            log.info('learn OK.\n')

            log.info('evaluate...')
            watch.start('evaluate...')
            _correct, _accuracy = sess.run([predicted, accuracy], feed_dict={X: test.features, Y: test.labels})  # Accuracy report
            watch.stop('evaluate...')
            log.info('evaluate OK.')

            log.info('model save... %s' % model_file)
            watch.start('model save...')
            model_dir = os.path.dirname(model_file)
            if not os.path.exists(model_dir):
                os.makedirs(model_dir)
            saver = tf.train.Saver()
            saver.save(sess, model_file)
            watch.stop('model save...')
            log.info('model save OK. %s' % model_file)

        log.info('\n')
        log.info(watch.summary())
        # log.info('hypothesis: %s %s' % (_hypothesis.shape, _hypothesis))
        # log.info('correct: %s %s' % (_correct.shape, _correct))
        log.info('accuracy: %s %s' % (_accuracy.shape, _accuracy))
        log.info('\n')

    @classmethod
    def sentence2features_labels(cls, sentence, left_gram=2, right_gram=2) -> (list, list):
        if left_gram < 1 or right_gram < 1:
            return [], []

        sentence = sentence.strip()
        if len(sentence) <= 1:
            return [], []

        labels = np.zeros(len(sentence.replace(' ', '')) - 1, dtype=np.int32)
        sentence = '%s%s%s' % (' ' * (left_gram - 1), sentence, ' ' * (right_gram - 1))
        idx = 0
        for mid in range(left_gram, len(sentence) - right_gram + 1):
            # log.debug('"%s" "%s" "%s"' % (sentence[mid - left_gram:mid], sentence[mid], sentence[mid + 1:mid + 1 + right_gram]))
            if sentence[mid] == ' ':
                labels[idx] = 1
            else:
                idx += 1

        no_space_sentence = '%s%s%s' % (' ' * (left_gram - 1), sentence.replace(' ', ''), ' ' * (right_gram - 1))
        features = []
        for i in range(left_gram, len(no_space_sentence) - right_gram + 1):
            a, b = no_space_sentence[i - left_gram: i], no_space_sentence[i: i + right_gram]
            features.append(a + b)
            # log.debug('[%d] "%s" "%s" %s' % (i-2, a, b, labels[i-2]))
        return features, labels.tolist()

    @classmethod
    def spacing(cls, sentence, labels):
        sentence = sentence.replace(' ', '')
        left = []
        for idx, right in enumerate(sentence[:-1]):
            left.append(right)
            if labels[idx]:
                left.append(' ')
        left.append(sentence[-1])
        return ''.join(left)

    @classmethod
    def build_FFNN(cls, n_features, n_classes, n_hidden1, learning_rate, watch=WatchUtil()):
        log.info('\nbuild_FFNN')
        if len(cls.graph_nodes) == 0:
            n_hidden3 = n_hidden2 = n_hidden1
            log.info('create tensorflow graph...')
            watch.start('create tensorflow graph')
            log.info('n_features: %s' % n_features)
            log.info('n_classes: %s' % n_classes)
            log.info('n_hidden1: %s' % n_hidden1)
            log.info('n_hidden2: %s' % n_hidden2)
            log.info('n_hidden3: %s' % n_hidden3)

            tf.set_random_seed(777)  # for reproducibility

            X = tf.placeholder(tf.float32, [None, n_features], name='X')  # two characters
            Y = tf.placeholder(tf.float32, [None, n_classes], name='Y')

            W1 = tf.Variable(tf.random_normal([n_features, n_hidden1]), name='W1')
            b1 = tf.Variable(tf.random_normal([n_hidden1]), name='b1')
            layer1 = tf.sigmoid(tf.matmul(X, W1) + b1, name='layer1')

            W2 = tf.Variable(tf.random_normal([n_hidden1, n_hidden2]), name='W2')
            b2 = tf.Variable(tf.random_normal([n_hidden2]), name='b2')
            layer2 = tf.sigmoid(tf.matmul(layer1, W2) + b2, name='layer2')

            W3 = tf.Variable(tf.random_normal([n_hidden2, n_hidden3]), name='W3')
            b3 = tf.Variable(tf.random_normal([n_hidden3]), name='b3')
            layer3 = tf.sigmoid(tf.matmul(layer2, W3) + b3, name='layer3')

            W4 = tf.Variable(tf.random_normal([n_hidden3, n_classes]), name='W4')
            b4 = tf.Variable(tf.random_normal([n_classes]), name='b4')
            hypothesis = tf.sigmoid(tf.matmul(layer3, W4) + b4, name='hypothesis')

            cost = -tf.reduce_mean(Y * tf.log(hypothesis) + (1 - Y) * tf.log(1 - hypothesis), name='cost')  # cost/loss function

            train_step = tf.train.AdamOptimizer(learning_rate=learning_rate).minimize(
                cost)  # Very Very good!! sentences=10000 + layer=4, 10분, accuracy 0.9294, cost: 0.1839

            predicted = tf.cast(hypothesis > 0.5, dtype=tf.float32, name='predicted')  # 0 <= hypothesis <= 1
            accuracy = tf.reduce_mean(tf.cast(tf.equal(predicted, Y), dtype=tf.float32), name='accuracy')
            watch.stop('create tensorflow graph')
            log.info('create tensorflow graph OK.\n')
            cls.graph_nodes = {'predicted': predicted, 'accuracy': accuracy, 'X': X, 'Y': Y, 'train_step': train_step, 'cost': cost}
        return cls.graph_nodes

    @classmethod
    def sim_two_sentence(cls, original, generated, left_gram=2, right_gram=2):
        _, labels1 = WordSpacing.sentence2features_labels(original, left_gram=left_gram, right_gram=right_gram)
        _, labels2 = WordSpacing.sentence2features_labels(generated, left_gram=left_gram, right_gram=right_gram)
        incorrect = 0
        for idx, l in enumerate(labels1):
            if l == 1 and labels2[idx] != 1:
                incorrect += 1

        total_spaces = labels1.count(1)  # 정답에 있는 공백 개수
        correct = total_spaces - incorrect  # 정답에 있는 공백과 같은 곳에 공백이 있는지

        if total_spaces == 0:
            sim = 1
        else:
            sim = correct / total_spaces
        return sim, correct, total_spaces


if __name__ == '__main__':
    train_sentences_file = KO_WIKIPEDIA_ORG_TRAIN_SENTENCES_FILE
    valid_sentences_file = KO_WIKIPEDIA_ORG_VALID_SENTENCES_FILE
    test_sentences_file = KO_WIKIPEDIA_ORG_TEST_SENTENCES_FILE
    log.info('train_sentences_file: %s' % train_sentences_file)
    log.info('valid_sentences_file: %s' % valid_sentences_file)
    log.info('test_sentences_file: %s' % test_sentences_file)

    characters_file = KO_WIKIPEDIA_ORG_CHARACTERS_FILE
    log.info('characters_file: %s' % characters_file)
    try:
        if len(sys.argv) == 4:
            n_train = int(sys.argv[1])
            left_gram = int(sys.argv[2])
            right_gram = int(sys.argv[3])
        else:
            n_train, left_gram, right_gram = None, None, None

        if n_train is None or n_train == 0:
            n_train = int('1,000,000'.replace(',', ''))  # 1M data (학습: 17시간 소요)

        if left_gram is None:
            left_gram = 2
        if right_gram is None:
            right_gram = 2

        ngram = left_gram + right_gram
        n_valid, n_test = 100, 100
        log.info('n_train: %s' % NumUtil.comma_str(n_train))
        log.info('n_valid: %s' % NumUtil.comma_str(n_valid))
        log.info('n_test: %s' % NumUtil.comma_str(n_test))
        log.info('left_gram: %s, right_gram: %s' % (left_gram, right_gram))
        log.info('ngram: %s' % ngram)

        total_sentences = FileUtil.count_lines(KO_WIKIPEDIA_ORG_SENTENCES_FILE)
        model_file = os.path.join(KO_WIKIPEDIA_ORG_WORD_SPACING_MODEL_DIR,
                                  'word_spacing_model.sentences=%s.left_gram=%s.right_gram=%s/model' % (
                                      n_train, left_gram, right_gram))  # .%s' % max_sentences
        log.info('model_file: %s' % model_file)

        batch_size = 1000  # mini batch size
        log.info('batch_size: %s' % batch_size)

        features_vector = OneHotVector(DataFileUtil.read_list(characters_file))
        labels_vector = OneHotVector([0, 1])  # 붙여쓰기=0, 띄어쓰기=1
        n_features = len(features_vector) * ngram  # number of features = 17,380 * 4
        n_classes = len(labels_vector) if len(labels_vector) >= 3 else 1  # number of classes = 2 but len=1
        n_hidden1 = 100
        learning_rate = 0.01  # 0.1 ~ 0.001
        log.info('features_vector: %s' % features_vector)
        log.info('labels_vector: %s' % labels_vector)
        log.info('n_features: %s' % n_features)
        log.info('n_classes: %s' % n_classes)
        log.info('n_hidden1: %s' % n_hidden1)
        log.info('learning_rate: %s' % learning_rate)

        # log.info('sample testing...')
        # test_set = ['예쁜 운동화', '즐거운 동화', '삼풍동 화재']
        # for s in test_set:
        #     features, labels = WordSpacing.sentence2features_labels(s, left_gram=left_gram, right_gram=right_gram)
        #     log.info('%s -> %s' % (features, labels))
        #     log.info('in : "%s"' % s)
        #     log.info('out: "%s"' % WordSpacing.spacing(s.replace(' ', ''), labels))
        # log.info('sample testing OK.\n')

        if not os.path.exists(model_file + '.index') or not os.path.exists(model_file + '.meta'):
            WordSpacing.learning(n_test, n_valid, n_test, batch_size, left_gram, right_gram, model_file, features_vector, labels_vector, n_hidden1=n_hidden1,
                                 learning_rate=learning_rate)

        log.info('chek result...')
        watch = WatchUtil()
        watch.start('read sentences')

        sentences = ['아버지가 방에 들어 가신다.', '가는 말이 고와야 오는 말이 곱다.']
        max_test_sentences = 100
        with gzip.open(test_sentences_file, 'rt') as f:
            for i, line in enumerate(f, 1):
                if len(sentences) >= max_test_sentences:
                    break

                s = line.strip()
                if s.count(' ') > 0:  # sentence must have one or more space.
                    sentences.append(s)
        log.info('len(sentences): %s' % NumUtil.comma_str(len(sentences)))
        watch.stop('read sentences')

        watch.start('run tensorflow')
        accuracies, sims = [], []
        with tf.Session() as sess:
            graph = WordSpacing.build_FFNN(n_features, n_classes, n_hidden1, learning_rate)
            X, Y, predicted, accuracy = graph['X'], graph['Y'], graph['predicted'], graph['accuracy']

            saver = tf.train.Saver()
            try:
                restored = saver.restore(sess, model_file)
            except:
                log.error('restore failed. model_file: %s' % model_file)
            try:
                for i, s in enumerate(sentences):
                    log.info('')
                    log.info('[%s] in : "%s"' % (i, s))
                    _features, _labels = WordSpacing.sentence2features_labels(s, left_gram, right_gram)  # TODO: with test set
                    dataset = DataSet(features=_features, labels=_labels, features_vector=features_vector, labels_vector=labels_vector)
                    dataset.convert_to_one_hot_vector()
                    if len(dataset) > 0:
                        _predicted, _accuracy = sess.run([predicted, accuracy], feed_dict={X: dataset.features, Y: dataset.labels})  # Accuracy report

                        sentence_hat = WordSpacing.spacing(s.replace(' ', ''), _predicted)
                        sim, correct, total = WordSpacing.sim_two_sentence(s, sentence_hat, left_gram=left_gram, right_gram=right_gram)

                        accuracies.append(_accuracy)
                        sims.append(sim)

                        log.info('[%s] out: "%s" (accuracy: %.1f%%, sim: %.1f%%=%s/%s)' % (i, sentence_hat, _accuracy * 100, sim * 100, correct, total))
            except:
                log.error(traceback.format_exc())

        log.info('chek result OK.')
        # noinspection PyStringFormat
        log.info('mean(accuracy): %.2f%%, mean(sim): %.2f%%' % (np.mean(accuracies) * 100, np.mean(sims) * 100))
        log.info('secs/sentence: %.4f' % (watch.elapsed('run tensorflow') / len(sentences)))
        log.info(watch.summary())
    except:
        log.error(traceback.format_exc())
