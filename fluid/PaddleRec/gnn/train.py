#  Copyright (c) 2019 PaddlePaddle Authors. All Rights Reserve.
#
#Licensed under the Apache License, Version 2.0 (the "License");
#you may not use this file except in compliance with the License.
#You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#See the License for the specific language governing permissions and
#limitations under the License.

import numpy as np
import os
from functools import partial
import logging
import paddle
import paddle.fluid as fluid
import argparse
import network
import reader

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("fluid")
logger.setLevel(logging.INFO)


def parse_args():
    parser = argparse.ArgumentParser("gnn")
    parser.add_argument(
        '--train_path', type=str, default='./data/diginetica/train.txt', help='dir of training data')
    parser.add_argument(
        '--config_path', type=str, default='./data/diginetica/config.txt', help='dir of config')
    parser.add_argument(
        '--model_path', type=str, default='./saved_model', help="path of model parameters")
    parser.add_argument(
        '--epoch_num', type=int, default=30, help='number of epochs to train for')
    parser.add_argument(
        '--batch_size', type=int, default=100, help='input batch size')
    parser.add_argument(
        '--hidden_size', type=int, default=100, help='hidden state size')
    parser.add_argument(
        '--l2', type=float, default=1e-5, help='l2 penalty')
    parser.add_argument(
        '--lr', type=float, default=0.001, help='learning rate')
    parser.add_argument(
        '--step', type=int, default=1, help='gnn propogation steps')
    parser.add_argument(
        '--lr_dc', type=float, default=0.1, help='learning rate decay rate')
    parser.add_argument(
        '--lr_dc_step', type=int, default=3, help='the number of steps after which the learning rate decay')
    parser.add_argument(
        '--use_cuda', type=int, default=0, help='whether to use gpu')
    parser.add_argument(
        '--use_parallel', type=int, default=1, help='whether to use parallel executor')
    return parser.parse_args()


def train():
    args = parse_args()
    batch_size = args.batch_size
    items_num = reader.read_config(args.config_path)
    loss, acc = network.network(batch_size, items_num, args.hidden_size,
                                args.step)

    data_reader = reader.Data(args.train_path, True)
    logger.info("load data complete")

    use_cuda = True if args.use_cuda else False
    use_parallel = True if args.use_parallel else False
    place = fluid.CUDAPlace(0) if use_cuda else fluid.CPUPlace()

    exe = fluid.Executor(place)
    step_per_epoch = data_reader.length // batch_size
    optimizer = fluid.optimizer.Adam(
        learning_rate=fluid.layers.exponential_decay(
            learning_rate=args.lr,
            decay_steps=step_per_epoch * args.lr_dc_step,
            decay_rate=args.lr_dc),
        regularization=fluid.regularizer.L2DecayRegularizer(
            regularization_coeff=args.l2))
    optimizer.minimize(loss)

    exe.run(fluid.default_startup_program())

    all_vocab = fluid.global_scope().var("all_vocab").get_tensor()
    all_vocab.set(
        np.arange(1, items_num).astype("int64").reshape((-1, 1)), place)

    feed_list = [
        "items", "seq_index", "last_index", "adj_in", "adj_out", "mask", "label"
    ]
    feeder = fluid.DataFeeder(feed_list=feed_list, place=place)

    if use_parallel:
        train_exe = fluid.ParallelExecutor(
            use_cuda=use_cuda, loss_name=loss.name)
    else:
        train_exe = exe

    logger.info("begin train")

    loss_sum = 0.0
    acc_sum = 0.0
    global_step = 0
    PRINT_STEP = 500
    for i in range(args.epoch_num):
        epoch_sum = []
        for data in data_reader.reader(batch_size, batch_size * 20, True):
            res = train_exe.run(feed=feeder.feed(data),
                                fetch_list=[loss.name, acc.name])
            loss_sum += res[0]
            acc_sum += res[1]
            epoch_sum.append(res[0])
            global_step += 1
            if global_step % PRINT_STEP == 0:
                logger.info("global_step: %d, loss: %.4lf, train_acc: %.4lf" % (
                    global_step, loss_sum / PRINT_STEP, acc_sum / PRINT_STEP))
                loss_sum = 0.0
                acc_sum = 0.0
        logger.info("epoch loss: %.4lf" % (np.mean(epoch_sum)))
        save_dir = args.model_path + "/epoch_" + str(i)
        fetch_vars = [loss, acc]
        fluid.io.save_inference_model(save_dir, feed_list, fetch_vars, exe)
        logger.info("model saved in " + save_dir)


if __name__ == "__main__":
    train()
