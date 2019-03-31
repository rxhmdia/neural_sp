#! /usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright 2018 Kyoto University (Hirofumi Inaguma)
#  Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)


"""Evaluate the ASR model."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import copy
import os
import time

from neural_sp.bin.args_asr import parse
from neural_sp.bin.train_utils import load_config
from neural_sp.bin.train_utils import set_logger
from neural_sp.datasets.loader_asr import Dataset
from neural_sp.evaluators.character import eval_char
from neural_sp.evaluators.phone import eval_phone
from neural_sp.evaluators.ppl import eval_ppl
from neural_sp.evaluators.word import eval_word
from neural_sp.evaluators.wordpiece import eval_wordpiece
from neural_sp.models.rnnlm.rnnlm import RNNLM
from neural_sp.models.seq2seq.seq2seq import Seq2seq


def main():

    args = parse()

    # Load a conf file
    dir_name = os.path.dirname(args.recog_model[0])
    conf = load_config(os.path.join(dir_name, 'conf.yml'))

    # Overwrite conf
    for k, v in conf.items():
        if 'recog' not in k:
            setattr(args, k, v)
    recog_params = vars(args)

    # Setting for logging
    if os.path.isfile(os.path.join(args.recog_dir, 'decode.log')):
        os.remove(os.path.join(args.recog_dir, 'decode.log'))
    logger = set_logger(os.path.join(args.recog_dir, 'decode.log'), key='decoding')

    wer_avg, cer_avg, per_avg = 0, 0, 0
    ppl_avg, loss_avg = 0, 0
    for i, s in enumerate(args.recog_sets):
        # Load dataset
        dataset = Dataset(corpus=args.corpus,
                          tsv_path=s,
                          dict_path=os.path.join(dir_name, 'dict.txt'),
                          dict_path_sub1=os.path.join(dir_name, 'dict_sub1.txt') if os.path.isfile(
                              os.path.join(dir_name, 'dict_sub1.txt')) else False,
                          dict_path_sub2=os.path.join(dir_name, 'dict_sub2.txt') if os.path.isfile(
                              os.path.join(dir_name, 'dict_sub2.txt')) else False,
                          dict_path_sub3=os.path.join(dir_name, 'dict_sub3.txt') if os.path.isfile(
                              os.path.join(dir_name, 'dict_sub3.txt')) else False,
                          nlsyms=os.path.join(dir_name, 'nlsyms.txt'),
                          wp_model=os.path.join(dir_name, 'wp.model'),
                          wp_model_sub1=os.path.join(dir_name, 'wp_sub1.model'),
                          wp_model_sub2=os.path.join(dir_name, 'wp_sub2.model'),
                          wp_model_sub3=os.path.join(dir_name, 'wp_sub3.model'),
                          unit=args.unit,
                          unit_sub1=args.unit_sub1,
                          unit_sub2=args.unit_sub2,
                          unit_sub3=args.unit_sub3,
                          batch_size=args.recog_batch_size,
                          concat_prev_n_utterances=args.recog_concat_prev_n_utterances,
                          is_test=True)

        if i == 0:
            # Load the ASR model
            model = Seq2seq(args)
            epoch = model.load_checkpoint(args.recog_model[0])['epoch']
            model.save_path = dir_name

            # ensemble (different models)
            ensemble_models = [model]
            if len(args.recog_model) > 1:
                for recog_model_e in args.recog_model[1:]:
                    # Load a conf file
                    conf_e = load_config(os.path.join(os.path.dirname(recog_model_e), 'conf.yml'))

                    # Overwrite conf
                    args_e = copy.deepcopy(args)
                    for k, v in conf_e.items():
                        if 'recog' not in k:
                            setattr(args_e, k, v)

                    model_e = Seq2seq(args_e)
                    model_e.load_checkpoint(recog_model_e)
                    model_e.cuda()
                    ensemble_models += [model_e]

            # For shallow fusion
            if not args.rnnlm_fusion:
                if args.recog_rnnlm is not None and args.recog_rnnlm_weight > 0:
                    # Load a RNNLM conf file
                    conf_rnnlm = load_config(os.path.join(os.path.dirname(args.recog_rnnlm), 'conf.yml'))

                    # Merge conf with args
                    args_rnnlm = argparse.Namespace()
                    for k, v in conf_rnnlm.items():
                        setattr(args_rnnlm, k, v)

                    # Load the pre-trianed RNNLM
                    rnnlm = RNNLM(args_rnnlm)
                    rnnlm.load_checkpoint(args.recog_rnnlm)
                    if args_rnnlm.backward:
                        model.rnnlm_bwd = rnnlm
                    else:
                        model.rnnlm_fwd = rnnlm

                if args.recog_rnnlm_bwd is not None and args.recog_rnnlm_weight > 0 and (args.recog_fwd_bwd_attention or args.recog_reverse_lm_rescoring):
                    # Load a RNNLM conf file
                    conf_rnnlm = load_config(os.path.join(args.recog_rnnlm_bwd, 'conf.yml'))

                    # Merge conf with args
                    args_rnnlm_bwd = argparse.Namespace()
                    for k, v in conf_rnnlm.items():
                        setattr(args_rnnlm_bwd, k, v)

                    # Load the pre-trianed RNNLM
                    rnnlm_bwd = RNNLM(args_rnnlm_bwd)
                    rnnlm_bwd.load_checkpoint(args.recog_rnnlm_bwd)
                    model.rnnlm_bwd = rnnlm_bwd

            if not args.recog_unit:
                args.recog_unit = args.unit

            logger.info('recog unit: %s' % args.recog_unit)
            logger.info('recog metric: %s' % args.recog_metric)
            logger.info('recog oracle: %s' % args.recog_oracle)
            logger.info('epoch: %d' % (epoch - 1))
            logger.info('batch size: %d' % args.recog_batch_size)
            logger.info('beam width: %d' % args.recog_beam_width)
            logger.info('min length ratio: %.3f' % args.recog_min_len_ratio)
            logger.info('max length ratio: %.3f' % args.recog_max_len_ratio)
            logger.info('length penalty: %.3f' % args.recog_length_penalty)
            logger.info('coverage penalty: %.3f' % args.recog_coverage_penalty)
            logger.info('coverage threshold: %.3f' % args.recog_coverage_threshold)
            logger.info('CTC weight: %.3f' % args.recog_ctc_weight)
            logger.info('RNNLM path: %s' % args.recog_rnnlm)
            logger.info('RNNLM path (bwd): %s' % args.recog_rnnlm_bwd)
            logger.info('RNNLM weight: %.3f' % args.recog_rnnlm_weight)
            logger.info('GNMT: %s' % args.recog_gnmt_decoding)
            logger.info('forward-backward attention: %s' % args.recog_fwd_bwd_attention)
            logger.info('reverse LM rescoring: %s' % args.recog_reverse_lm_rescoring)
            logger.info('resolving UNK: %s' % args.recog_resolving_unk)
            logger.info('ensemble: %d' % (len(ensemble_models)))
            logger.info('ASR decoder state carry over: %s' % (args.recog_asr_state_carry_over))
            logger.info('RNNLM state carry over: %s' % (args.recog_rnnlm_state_carry_over))
            logger.info('cache size: %d' % (args.recog_n_caches))
            logger.info('cache type: %s' % (args.recog_cache_type))
            logger.info('cache theta (speech): %.3f' % (args.recog_cache_theta_speech))
            logger.info('cache lambda (speech): %.3f' % (args.recog_cache_lambda_speech))
            logger.info('cache theta (lm): %.3f' % (args.recog_cache_theta_lm))
            logger.info('cache lambda (lm): %.3f' % (args.recog_cache_lambda_lm))
            logger.info('concat_prev_n_utterances: %d' % (args.recog_concat_prev_n_utterances))

            # GPU setting
            model.cuda()

        start_time = time.time()

        if args.recog_metric == 'edit_distance':
            if args.recog_unit in ['word', 'word_char']:
                (wer, _, _, _), (cer, _, _, _) = eval_word(ensemble_models, dataset, recog_params,
                                                           epoch=epoch - 1,
                                                           recog_dir=args.recog_dir,
                                                           progressbar=True)[0]
                wer_avg += wer
            elif args.recog_unit == 'wp':
                (wer, _, _, _), (cer, _, _, _) = eval_wordpiece(
                    ensemble_models, dataset, recog_params,
                    epoch=epoch - 1,
                    recog_dir=args.recog_dir,
                    progressbar=True)
                wer_avg += wer
                cer_avg += cer
            elif 'char' in args.recog_unit:
                (wer, _, _, _), (cer, _, _, _) = eval_char(
                    ensemble_models, dataset, recog_params,
                    epoch=epoch - 1,
                    recog_dir=args.recog_dir,
                    progressbar=True,
                    task_idx=1 if args.recog_unit and 'char' in args.recog_unit else 0)
                wer_avg += wer
                cer_avg += cer
            elif 'phone' in args.recog_unit:
                per = eval_phone(ensemble_models, dataset, recog_params,
                                 epoch=epoch - 1,
                                 recog_dir=args.recog_dir,
                                 progressbar=True)[0]
                per_avg += per
            else:
                raise ValueError(args.recog_unit)
        elif args.recog_metric == 'acc':
            raise NotImplementedError
        elif args.recog_metric in ['ppl', 'loss']:
            ppl, loss = eval_ppl(ensemble_models, dataset,
                                 recog_params=recog_params,
                                 progressbar=True)
            ppl_avg += ppl
            loss_avg += loss
        elif args.recog_metric == 'bleu':
            raise NotImplementedError
        else:
            raise NotImplementedError
        logger.info('Elasped time: %.2f [sec]:' % (time.time() - start_time))

    if args.recog_metric == 'edit_distance':
        if 'phone' in args.recog_unit:
            logger.info('PER (avg.): %.2f %%\n' % (per_avg / len(args.recog_sets)))
        else:
            logger.info('WER / CER (avg.): %.2f / %.2f %%\n' %
                        (wer_avg / len(args.recog_sets), cer_avg / len(args.recog_sets)))
    elif args.recog_metric in ['ppl', 'loss']:
        logger.info('PPL (avg.): %.2f\n' % (ppl_avg / len(args.recog_sets)))
        print('PPL (avg.): %.2f' % (ppl_avg / len(args.recog_sets)))
        logger.info('Loss (avg.): %.2f\n' % (loss_avg / len(args.recog_sets)))
        print('Loss (avg.): %.2f' % (loss_avg / len(args.recog_sets)))


if __name__ == '__main__':
    main()
