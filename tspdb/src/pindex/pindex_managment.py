import numpy as np
import pandas as pd
from tspdb.src.database_module.db_class import Interface
from  tspdb.src.prediction_models.ts_meta_model import TSMM
from  tspdb.src.prediction_models.ts_svd_model import SVDModel
from math import ceil
from tspdb.src.pindex.predict import get_prediction_range, get_prediction
import os
from datetime import datetime
from tspdb.src.pindex.pindex_utils import index_ts_mapper, index_ts_inv_mapper, index_exists, get_bound_time


def delete_pindex(db_interface, index_name, schema='tspdb'):
    """
    Delete Pindex index_name from database.
    ----------
    Parameters
    ----------
    db_interface: DBInterface object
        instant of an interface with the db
    
    index_name: string 
        name of the pindex to be deleted

    schema: string 
        name of the tspdb schema
    """

    # suffixes of the pindex tables
    suffix = ['u', 'v', 's', 'm', 'c', 'meta']
    index_name_ = schema + '.' + index_name

    meta_table = index_name_ + "_meta"
    # get time series table name
    table_name = db_interface.query_table(meta_table, columns_queried=['time_series_table_name'])[0][0]

    # drop mean and variance tables 
    for suf in suffix:
        db_interface.drop_table(index_name_ + '_' + suf)
        db_interface.drop_table(index_name_ + '_variance_' + suf)

    # drop pindex data from pindices and oindices_coumns tables and the insert trigger on table_name
    db_interface.delete('tspdb.pindices', "index_name = '" + str(index_name) + "';")
    db_interface.delete('tspdb.pindices_columns', "index_name = '" + str(index_name) + "';")
    db_interface.delete('tspdb.pindices_stats', "index_name = '" + str(index_name) + "';")
    
    db_interface.drop_trigger(table_name)


def load_pindex(db_interface, index_name, _dir=''):
    """
    load Pindex index_name from database.
    ----------
    Parameters
    ----------
    db_interface: DBInterface object
        instant of an interface with the db

    index_name: string 
        name of the pindex to be deleted

    dir: string optional
        directory from which some model information are loaded (it will be loaded from DB if not available)
    ----------
    Returns
    ----------
    pindex a TSPI object
    """

    ##################################### TO DO ################################
    # 1 load all meta table at once
    # 2 remove temp fixes
    ############################################################################

    # query meta table
    meta_table = index_name + "_meta"
    meta_inf = db_interface.query_table(meta_table,
                                        columns_queried=['T', 'T0', 'k', 'gamma', 'var_direct_method', 'k_var', 'T_var',
                                                         'soft_thresholding', 'start_time', 'aggregation_method',
                                                         'agg_interval', 'time_series_table_name', 'indexed_column',
                                                         'time_column'])

    T, T0, k, gamma, direct_var, k_var, T_var, SSVT, start_time, aggregation_method, agg_interval = meta_inf[0][:-3]

    # ------------------------------------------------------
    # temp fix
    gamma = float(gamma)
    if not isinstance(start_time, (int, np.integer)):
        start_time = pd.to_datetime(start_time)
    agg_interval = float(agg_interval)
    # ------------------------------------------------------

    time_series_table = meta_inf[0][-3:]
    TSPD = TSPI(interface=db_interface, index_name=index_name, schema=None, T=T, T0=T0, rank=k, gamma=gamma,
                direct_var=direct_var, rank_var=k_var, T_var=T_var, SSVT=SSVT, start_time=start_time,
                aggregation_method=aggregation_method, agg_interval=agg_interval, time_series_table=time_series_table)

    # load meta for specific models

    col_to_row_ratio, L, ReconIndex, MUpdateIndex, TimeSeriesIndex = \
        db_interface.query_table(meta_table, columns_queried=['col_to_row_ratio',
                                                              'L',
                                                              'last_TS_fullSVD',
                                                              'last_TS_inc',
                                                              'last_TS_seen'])[0]
    TSPD.ts_model = TSMM(TSPD.k, TSPD.T, TSPD.gamma, TSPD.T0, col_to_row_ratio=col_to_row_ratio,
                         model_table_name=index_name, SSVT=TSPD.SSVT, L=L)
    TSPD.ts_model.ReconIndex, TSPD.ts_model.MUpdateIndex, TSPD.ts_model.TimeSeriesIndex = ReconIndex, MUpdateIndex, TimeSeriesIndex

    # load variance models if any
    if TSPD.k_var != 0:
        col_to_row_ratio, L, ReconIndex, MUpdateIndex, TimeSeriesIndex = db_interface.query_table(meta_table,
                                                                                                  columns_queried=[
                                                                                                      'col_to_row_ratio_var',
                                                                                                      'L_var',
                                                                                                      'last_TS_fullSVD_var',
                                                                                                      'last_TS_inc_var',
                                                                                                      'last_TS_seen_var'])[
            0]

        TSPD.var_model = TSMM(TSPD.k_var, TSPD.T_var, TSPD.gamma, TSPD.T0, col_to_row_ratio=col_to_row_ratio,
                              model_table_name=index_name + "_variance", SSVT=TSPD.SSVT, L=L, )
        TSPD.var_model.ReconIndex, TSPD.var_model.MUpdateIndex, TSPD.var_model.TimeSeriesIndex = ReconIndex, MUpdateIndex, TimeSeriesIndex

    # query models table

    TSPD._load_models_from_db(TSPD.ts_model)

    # load last T points of a time series (from .npy file (faster less reliable), if failed load from db)
    try:
        TSPD.ts_model.TimeSeries = np.load(_dir + index_name + '_ts.npy')
        os.remove(_dir + index_name + '_ts.npy')
    except:

        start_point = index_ts_inv_mapper(TSPD.start_time, TSPD.agg_interval,
                                          TSPD.ts_model.TimeSeriesIndex - TSPD.ts_model.T)
        end_point = index_ts_inv_mapper(TSPD.start_time, TSPD.agg_interval, TSPD.ts_model.TimeSeriesIndex - 1)

        TSPD.ts_model.TimeSeries = TSPD._get_range(start_point, end_point)

    # query variance models table

    if TSPD.k_var != 0:
        TSPD._load_models_from_db(TSPD.var_model)

        # load last T points of  variance time series (squared of observations if not direct_var)
        if TSPD.direct_var:
            try:
                TSPD.var_model.TimeSeries = np.load(_dir + index_name + '_var.npy')
                os.remove(_dir + index_name + '_var.npy')
            except:
                with open('psql-output.txt', 'a') as o:
                    o.write('db loading2')

                start_point = index_ts_inv_mapper(TSPD.start_time, TSPD.agg_interval,
                                                  max(TSPD.var_model.TimeSeriesIndex - TSPD.var_model.T, 0))
                end_point = index_ts_inv_mapper(TSPD.start_time, TSPD.agg_interval, TSPD.var_model.TimeSeriesIndex - 1)

                mean = get_prediction_range(index_name, TSPD.time_series_table[0], TSPD.time_series_table[1],
                                            TSPD.time_series_table[2], db_interface, start_point, end_point, uq=False)
                with open('psql-output.txt', 'a') as o:
                    o.write(str(TSPD._get_range(start_point, end_point)) + ',' + str(start_point) + ',' + str(
                        end_point) + '\n')
                TSPD.var_model.TimeSeries = TSPD._get_range(start_point, end_point) - mean


        else:
            TSPD.var_model.TimeSeries = (TSPD.ts_model.TimeSeries) ** 2

    return TSPD


class TSPI(object):
    # k:                        (int) the number of singular values to retain in the means prediction model
    # k_var:                    (int) the number of singular values to retain in the variance prediction model
    # T0:                       (int) the number of entries below which the model will not be trained
    # T:                        (int) Number of entries in each submodel in the means prediction model
    # T_var:                    (int) Number of entries in each submodel in the variance prediction model
    # gamma:                    (float) (0,1) fraction of T after which the last sub-model is fully updated
    # rectFactor:               (int) the ration of no. columns to the number of rows in each sub-model
    # L:                        (int) the number of rows in each sub-model. if set, rectFactor is ignored.
    # DBinterface:              (DBInterface object) the object used in communicating with the database.
    # table_name:               (str) The name of the time series table in the database
    # time_column:              (str) The name of the time column in the time series table in the database
    # value_column:             (str) The name of the value column in the time series table in the database
    # var_method_diff:          (bol) if True, calculate variance by subtracting the mean from the observations in the variance prediction model
    # mean_model:               (TSMM object) the means prediction model object
    # var_model:                (TSMM object) the variance prediction model object

    def __init__(self, rank=3, rank_var=1, T=int(1e5), T_var=None, gamma=0.2, T0=1000, col_to_row_ratio=10,
                 interface=Interface, agg_interval=1., start_time=None, aggregation_method='average',
                 time_series_table=["", "", ""], SSVT=False, p=1.0, direct_var=True, L=None, recreate=True,
                 index_name=None, _dir='', schema='tspdb'):
        self._dir = _dir
        self.index_ts = False


        self.db_interface = interface
        self.time_series_table = time_series_table
        self.k = rank
        self.T = int(T)
        self.SSVT = SSVT
        if T_var is None:
            self.T_var = self.T
        else:
            self.T_var = T_var

        self.gamma = gamma
        self.T0 = T0
        self.k_var = rank_var
        self.index_name = index_name
        self.schema = schema
        self.agg_interval = agg_interval
        
        self.aggregation_method = aggregation_method
        self.start_time = start_time
        self.tz = None

        if self.start_time is None:
            self.start_time = get_bound_time(interface, self.time_series_table[0], self.time_series_table[2], 'min')
        
        if self.index_name is None:
            self.index_name = self.schema + '.pindex_' + time_series_table[0]
        
        elif schema is not None:
            self.index_name = self.schema + '.' + self.index_name

        if isinstance(self.start_time, (int, np.integer)):
            self.agg_interval = 1.

        self.ts_model = TSMM(self.k, self.T, self.gamma, self.T0, col_to_row_ratio=col_to_row_ratio,
                             model_table_name=self.index_name, SSVT=self.SSVT, p=p, L=L)
        self.var_model = TSMM(self.k_var, self.T_var, self.gamma, self.T0, col_to_row_ratio=col_to_row_ratio,
                              model_table_name=self.index_name + "_variance", SSVT=self.SSVT, p=p,
                              L=L)
        self.direct_var = direct_var

        if self.k_var:
            self.uq = True
        else:
            self.uq = False

    def create_index(self):
        """
        This function query new datapoints from the database using the variable self.TimeSeriesIndex and call the
        update_model function
        """
        # find starting and ending time 
        end_point = get_bound_time(self.db_interface, self.time_series_table[0], self.time_series_table[2], 'max')
        start_point = index_ts_inv_mapper(self.start_time, self.agg_interval, self.ts_model.TimeSeriesIndex)

        # ------------------------------------------------------
        # why np.array ? delete when value_column  check is implemeted
        # ------------------------------------------------------

        new_entries = np.array(self._get_range(start_point, end_point), dtype=np.float)

        if len(new_entries) > 0:
            self.update_model(new_entries)
            self.write_model(True)
        self.db_interface.drop_trigger(self.time_series_table[0])
        self.db_interface.create_insert_trigger(self.time_series_table[0], self.index_name)

    def update_index(self):
        """
        This function query new datapoints from the database using the variable self.TimeSeriesIndex and call the
        update_model function
        """

        end_point = get_bound_time(self.db_interface, self.time_series_table[0], self.time_series_table[2], 'max')
        start_point = index_ts_inv_mapper(self.start_time, self.agg_interval, self.ts_model.TimeSeriesIndex)
        new_entries = self._get_range(start_point, end_point)
        if len(new_entries) > 0:
            self.update_model(new_entries)
            self.write_model(False)

    def update_model(self, NewEntries):
        """
        This function takes a new set of entries and update the model accordingly.
        if the number of new entries means new model need to be bulit, this function segment the new entries into
        several entries and then feed them to the update_ts and fit function
        :param NewEntries: Entries to be included in the new model
        """
        # ------------------------------------------------------
        # is it already numpy.array? ( not really needed but not harmful)
        obs = np.array(NewEntries)
        # ------------------------------------------------------

        # lag is the the slack between the variance and timeseries model        
        lag = None

        if self.ts_model.TimeSeries is not None:
            lag = self.ts_model.TimeSeriesIndex - self.var_model.TimeSeriesIndex
            lagged_obs = self.ts_model.TimeSeries[- lag:]

        # Update mean model
        self.ts_model.update_model(NewEntries)

        # Determine updated models
        models = {k: self.ts_model.models[k] for k in self.ts_model.models if self.ts_model.models[k].updated}

        if self.k_var:
            if self.direct_var:

                means = self.ts_model._denoiseTS(models)[self.var_model.TimeSeriesIndex:self.ts_model.MUpdateIndex]
                if lag is not None:
                    var_obs = np.append(lagged_obs, obs)
                else:
                    var_obs = obs

                var_entries = np.square(var_obs[:len(means)] - means)

                # ------------------------------------------------------
                # EDIT: Is this necessary (NAN to zero)?
                # var_entries[np.isnan(var_obs[:len(means)])] = 0    
                # ------------------------------------------------------

                self.var_model.update_model(var_entries)

            else:

                var_entries = np.square(NewEntries)
                self.var_model.update_model(var_entries)

    def write_model(self, create):
        """
        write the pindex to db
        ----------
        Parameters
        ----------
        create: bol 
            if Ture, create the index in DB, else update it.
        """

        # remove schema name if exist
        index_name = self.index_name.split('.')[1]

        # delete meta data if create
        if create:
            self.db_interface.delete('tspdb.pindices', "index_name = '" + str(index_name) + "';")
            self.db_interface.delete('tspdb.pindices', "index_name = '" + str(index_name) + "';")
            self.db_interface.delete('tspdb.pindices_stats', "index_name = '" + str(index_name) + "';")

        # write mean and variance tables
        self.write_tsmm_model(self.ts_model, create)
        self.write_tsmm_model(self.var_model, create)
        
        # if time is timestamp, convert to pd.Timestamp
        if not isinstance(self.start_time, (int, np.integer)):
            self.start_time = pd.to_datetime(self.start_time)

        # prepare meta data table
        metadf = pd.DataFrame(
            data={'T': [self.ts_model.T], 'T0': [self.T0], 'gamma': [float(self.gamma)], 'k': [self.k],
                  'L': [self.ts_model.L],
                  'last_TS_seen': [self.ts_model.TimeSeriesIndex], 'last_TS_inc': [self.ts_model.MUpdateIndex],
                  'last_TS_fullSVD': [self.ts_model.ReconIndex],
                  'time_series_table_name': [self.time_series_table[0]], 'indexed_column': [self.time_series_table[1]],
                  'time_column': [self.time_series_table[2]],
                  'soft_thresholding': [self.SSVT], 'no_submodels': [len(self.ts_model.models)],
                  'no_submodels_var': [len(self.var_model.models)],
                  'col_to_row_ratio': [self.ts_model.col_to_row_ratio],
                  'col_to_row_ratio_var': [self.var_model.col_to_row_ratio], 'T_var': [self.var_model.T],
                  'k_var': [self.k_var], 'L_var': [self.var_model.L],
                  'last_TS_seen_var': [self.var_model.TimeSeriesIndex],
                  'last_TS_inc_var': [self.var_model.MUpdateIndex], 'aggregation_method': [self.aggregation_method],
                  'agg_interval': [self.agg_interval],
                  'start_time': [self.start_time], 'last_TS_fullSVD_var': [self.var_model.ReconIndex],
                  'var_direct_method': [self.direct_var]})
        
        # ------------------------------------------------------
        # EDIT: Due to some incompatibiliy with PSQL timestamp types 
        # Further investigate 
        # ------------------------------------------------------
        if not isinstance(self.start_time, (int, np.integer)):
            #metadf['start_time'] = metadf['start_time'].astype(pd.Timestamp)
            metadf['start_time'] = metadf['start_time'].astype('datetime64[ns]')
        
        if create:
            # create meta table
            self.db_interface.create_table(self.index_name + '_meta', metadf, include_index=False)
            last_index = index_ts_inv_mapper(self.start_time, self.agg_interval, self.ts_model.TimeSeriesIndex)
            # populate tspdb pindices and column pindices
            if isinstance(self.start_time, (int, np.integer)):
                self.db_interface.insert('tspdb.pindices',
                                         [index_name, self.time_series_table[0], self.time_series_table[2], self.uq,
                                          self.agg_interval, self.start_time, last_index],
                                         columns=['index_name', 'relation', 'time_column', 'uq', 'agg_interval',
                                                  'initial_index', 'last_index'])
            else:
                self.db_interface.insert('tspdb.pindices',
                                         [index_name, self.time_series_table[0], self.time_series_table[2], self.uq,
                                          self.agg_interval, self.start_time, last_index],
                                         columns=['index_name', 'relation', 'time_column', 'uq', 'agg_interval',
                                                  'initial_timestamp', 'last_timestamp'])
            self.db_interface.insert('tspdb.pindices_stats',
                                         [index_name, self.ts_model.TimeSeriesIndex, len(self.ts_model.models),np.mean([ m.imputation_model_score for m in self.ts_model.models.values() ]), np.mean([ m.forecast_model_score for m in self.ts_model.models.values()])],
                                         columns=['index_name', 'number_of_observations', 'number_of_trained_models', 'imputation_score', 'forecast_score'])
            self.db_interface.insert('tspdb.pindices_columns', [index_name, self.time_series_table[1]],
                                     columns=['index_name', 'value_column'])

        else:
            # else update meta table, tspdb pindices 
            self.db_interface.delete(self.index_name + '_meta', '')
            self.db_interface.insert(self.index_name + '_meta', metadf.iloc[0])
            self.db_interface.delete('tspdb.pindices', "index_name = '" + str(index_name) + "';")
            self.db_interface.delete('tspdb.pindices_stats', "index_name = '" + str(index_name) + "';")
            
            last_index = index_ts_inv_mapper(self.start_time, self.agg_interval, self.ts_model.TimeSeriesIndex)
            self.db_interface.insert('tspdb.pindices_stats',
                                         [index_name, self.ts_model.TimeSeriesIndex, len(self.ts_model.models),np.mean([ m.imputation_model_score for m in self.ts_model.models.values() ]), np.mean([ m.forecast_model_score for m in self.ts_model.models.values()])],
                                         columns=['index_name', 'number_of_observations', 'number_of_trained_models', 'imputation_score', 'forecast_score'])
            
            if isinstance(self.start_time, (int, np.integer)):
                self.db_interface.insert('tspdb.pindices',
                                         [index_name, self.time_series_table[0], self.time_series_table[2], self.uq,
                                          self.agg_interval, self.start_time, last_index],
                                         columns=['index_name', 'relation', 'time_column', 'uq', 'agg_interval',
                                                  'initial_index', 'last_index'])
            else:
                self.db_interface.insert('tspdb.pindices',
                                         [index_name, self.time_series_table[0], self.time_series_table[2], self.uq,
                                          self.agg_interval, self.start_time, last_index],
                                         columns=['index_name', 'relation', 'time_column', 'uq', 'agg_interval',
                                                  'initial_timestamp', 'last_timestamp'])

        # store numpy arrays in dir_ ro be retrieved later (not necessary, but speed things up) 
        np.save(self._dir + self.index_name + '_ts.npy', self.ts_model.TimeSeries)
        np.save(self._dir + self.index_name + '_var.npy', self.var_model.TimeSeries)

    def write_tsmm_model(self, tsmm, create):
        """
        -
        """
        ########################### To Do ######################
        # 1 Replace for loops with vectorized numpy operations
        ########################################################

        # only get updated sub models
        models = {k: tsmm.models[k] for k in tsmm.models if tsmm.models[k].updated}

        # Mo
        model_name = tsmm.model_tables_name

        if len(models) == 0:
            return

        N = tsmm.L
        M = int(N * tsmm.col_to_row_ratio)
        tableNames = [model_name + '_' + c for c in ['u', 'v', 's', 'c', 'm']]

        last_model = max(models.keys())
        first_model = min(models.keys())

        # populate U_table data
        U_table = np.zeros([(len(models) - 1) * N + models[last_model].N, 1 + tsmm.kSingularValuesToKeep])
        for i, m in models.items():
            j = i - first_model
            if i == last_model:
                U_table[j * N:, 1:1 + tsmm.kSingularValuesToKeep] = m.Uk
                U_table[j * N:, 0] = int(i)
            else:
                U_table[j * N:(j + 1) * N, 1:1 + tsmm.kSingularValuesToKeep] = m.Uk
                U_table[j * N:(j + 1) * N, 0] = int(i)

        columns = ['modelno'] + ['u' + str(i) for i in range(1, tsmm.kSingularValuesToKeep + 1)]
        udf = pd.DataFrame(columns=columns, data=U_table)
        udf.index = np.arange(first_model * N, first_model * N + len(U_table))
        udf['tsrow'] = (udf.index % N).astype(int)

        if create:
            self.db_interface.create_table(tableNames[0], udf, 'row_id', index_label='row_id')
        else:
            self.db_interface.delete(tableNames[0], 'modelno >= %s and modelno <= %s' % (first_model, last_model,))
            self.db_interface.bulk_insert(tableNames[0], udf, index_label='row_id')

        # populate V_table data
        V_table = np.zeros([(len(models) - 1) * M + models[last_model].M, 1 + tsmm.kSingularValuesToKeep])
        for i, m in models.items():
            j = i - first_model
            if i == last_model:
                V_table[j * M:, 1:1 + tsmm.kSingularValuesToKeep] = m.Vk
                V_table[j * M:, 0] = int(i)

            else:
                V_table[j * M:(j + 1) * M, 1:1 + tsmm.kSingularValuesToKeep] = m.Vk
                V_table[j * M:(j + 1) * M, 0] = int(i)

        columns = ['modelno'] + ['v' + str(i) for i in range(1, tsmm.kSingularValuesToKeep + 1)]
        vdf = pd.DataFrame(columns=columns, data=V_table)
        vdf.index = np.arange(first_model * M, first_model * M + len(V_table))
        vdf['tscolumn'] = (vdf.index - 0.5 * M * vdf['modelno']).astype(int)
        if create:
            self.db_interface.create_table(tableNames[1], vdf, 'row_id', index_label='row_id')
        else:
            self.db_interface.delete(tableNames[1], 'modelno >= %s and modelno <= %s' % (first_model, last_model,))
            self.db_interface.bulk_insert(tableNames[1], vdf, index_label='row_id')

        # populate s_table data 
        s_table = np.zeros([len(models), 1 + tsmm.kSingularValuesToKeep])
        for i, m in models.items():
            j = i - first_model
            s_table[j, 1:tsmm.kSingularValuesToKeep + 1] = m.sk
            s_table[j, 0] = int(i)
        columns = ['modelno'] + ['s' + str(i) for i in range(1, tsmm.kSingularValuesToKeep + 1)]
        sdf = pd.DataFrame(columns=columns, data=s_table)
        if create:
            self.db_interface.create_table(tableNames[2], sdf, 'modelno', include_index=False, index_label='row_id')
        else:
            self.db_interface.delete(tableNames[2], 'modelno >= %s and modelno <= %s' % (first_model, last_model,))
            self.db_interface.bulk_insert(tableNames[2], sdf, include_index=False)

        # populate c_table data 
        id_c = 0
        w_f = N - 1
        w_l = len(models[last_model].weights)
        c_table = np.zeros([(len(models) - 1) * w_f + w_l, 3])
        for i, m in models.items():
            coeNu = 0
            for weig in m.weights:
                c_table[id_c, :] = [i, coeNu, weig]
                id_c += 1
                coeNu += 1

        cdf = pd.DataFrame(columns=['modelno', 'coeffpos', 'coeffvalue'], data=c_table)
        cdf.index = np.arange(first_model * w_f, first_model * w_f + len(c_table))

        if create:
            self.db_interface.create_table(tableNames[3], cdf, 'row_id', index_label='row_id')
        else:
            self.db_interface.delete(tableNames[3], 'modelno >= %s and modelno <= %s' % (first_model, last_model,))
            self.db_interface.bulk_insert(tableNames[3], cdf, include_index=True, index_label="row_id")

        # populate m_table data 
        m_table = np.zeros([len(models), 9])
        for i, m in models.items():
            m_table[i - first_model, :] = [i, m.N, m.M, m.start, m.M * m.N, m.TimesUpdated, m.TimesReconstructed, m.imputation_model_score, m.forecast_model_score]
        mdf = pd.DataFrame(columns=['modelno', 'L', 'N', 'start', 'dataPoints', 'timesUpdated', 'timesRecons','imputation_acc', 'forecasting_acc'],
                           data=m_table)
        if create:
            self.db_interface.create_table(tableNames[4], mdf, 'modelno', include_index=False, index_label='modelno')
        else:
            self.db_interface.delete(tableNames[4], 'modelno >= %s and modelno <= %s' % (first_model, last_model,))
            self.db_interface.bulk_insert(tableNames[4], mdf, include_index=False)

        if create:
            self.db_interface.create_index(tableNames[0], 'tsrow, modelno')
            self.db_interface.create_index(tableNames[0], 'modelno')
            self.db_interface.create_index(tableNames[1], 'tscolumn, modelno')
            self.db_interface.create_index(tableNames[1], 'modelno')
            self.db_interface.create_index(tableNames[2], 'modelno')
            self.db_interface.create_index(tableNames[3], 'modelno')
            self.db_interface.create_index(tableNames[3], 'coeffpos')
            self.db_interface.create_coefficients_average_table(tableNames[3], tableNames[3] + '_view', [10, 20, 100],
                                                                last_model)
            self.db_interface.create_index(tableNames[3] + '_view', 'coeffpos')
        else:
            last_model = len(tsmm.models) - 1
            self.db_interface.create_coefficients_average_table(tableNames[3], tableNames[3] + '_view', [10, 20, 100],
                                                                last_model, refresh=True)

    def _load_models_from_db(self, tsmm):

        models_info_table = tsmm.model_tables_name + '_m'
        info = self.db_interface.query_table(models_info_table, columns_queried=['modelno',
                                                                                 'L',
                                                                                 'N',
                                                                                 'start',
                                                                                 'timesUpdated',
                                                                                 'timesRecons'])
        for model in info:
            tsmm.models[int(model[0])] = SVDModel('t1', tsmm.kSingularValuesToKeep, int(model[1]), int(model[2]),
                                                  start=int(model[3]),
                                                  TimesReconstructed=int(model[4]),
                                                  TimesUpdated=int(model[5]), SSVT=tsmm.SSVT, probObservation=tsmm.p,
                                                  updated=False)

        # load last model
        last_model = len(tsmm.models) - 1
        tsmm.models[last_model].sk = \
            self.db_interface.get_S_row(tsmm.model_tables_name + '_s', [last_model, last_model],
                                        tsmm.kSingularValuesToKeep)[0]
        tsmm.models[last_model].Uk = self.db_interface.get_U_row(tsmm.model_tables_name + '_u', [0, 2 * tsmm.L],
                                                                 [last_model, last_model], tsmm.kSingularValuesToKeep)
        tsmm.models[last_model].Vk = self.db_interface.get_V_row(tsmm.model_tables_name + '_v',
                                                                 [0, tsmm.TimeSeriesIndex], tsmm.kSingularValuesToKeep,
                                                                 [last_model, last_model])

        tsmm.models[last_model].skw = tsmm.models[last_model].sk
        tsmm.models[last_model].Ukw = tsmm.models[last_model].Uk[:-1, :]
        tsmm.models[last_model].Vkw = tsmm.models[last_model].Vk

    def _get_range(self, t1, t2=None):
        """
        implement the same singles point query. use get from table function in interface
        """

        return np.array([i[0] for i in self.db_interface.get_time_series(self.time_series_table[0], t1, t2,
                                                                         value_column=self.time_series_table[1],
                                                                         index_column=self.time_series_table[2],
                                                                         aggregation_method=self.aggregation_method,
                                                                         interval=self.agg_interval,
                                                                         start_ts=self.start_time)])

    #####################################
    # DELETE (Not needed anymore)
    #####################################

    def _get_forecast_range_local(self, t1, t2, model, input=None):
        coeffs = np.mean(np.array([m.weights for m in model.models.values()[:10]]), 0)
        no_coeff = len(coeffs)
        output = np.zeros([t2 - t1 + 1 + no_coeff])

        if input is None:
            output[:no_coeff] = self.get_imputation_range_local(t1 - no_coeff, t1 - 1, model)
        else:
            output[:no_coeff] = input[:]
        for i in range(0, t2 + 1 - t1):
            output[i + no_coeff] = np.dot(coeffs.T, output[i:i + no_coeff])
            # output[i + no_coeff] = sum([a[0]*b for a, b in zip(coeffs,output[i:i + no_coeff])])
        return output[-(t2 - t1 + 1):]

    def _get_imputation_range_local(self, t1, t2, model):

        m1 = model.get_model_index(t1)
        m2 = model.get_model_index(t2)
        N1 = model.models[m1].N
        N2 = model.models[m2].N
        M1 = model.models[m1].M

        tscol2 = int((t2 - model.models[m1].start) / N1)
        tsrow2 = int((t2 - model.models[m1].start) % N1)
        tscol1 = int((t1 - model.models[m1].start) / N1)
        tsrow1 = int((t1 - model.models[m1].start) % N1)
        i_index = (t1 - t1 % N1) + tsrow1
        last_model = len(model.models) - 1
        if m1 == m2:
            if tscol1 != tscol2:
                U1 = model.models[m1].Uk[:, :]
            else:
                U1 = model.models[m1].Uk[tsrow1:tsrow2 + 1, :]

            S1 = model.models[m1].sk[:]
            V1 = model.models[m1].Vk[tscol1:tscol2 + 1, :]

            p1 = np.dot(U1 * S1[:], V1[:].T)
            if (m1 < last_model - 1 and m1 != 0):
                S2 = model.models[m1 + 1].sk[:]
                V2 = model.models[m1 + 1].Vk[tscol1 - int(M1 / 2):tscol2 - int(M1 / 2) + 1, :]
                if tscol1 != tscol2:
                    U2 = model.models[m1 + 1].Uk[:, :]
                else:
                    U2 = model.models[m1 + 1].Uk[tsrow1:tsrow2 + 1, :]

                Result = 0.5 * p1.T.flatten() + 0.5 * np.dot(U2 * S2[:], V2[:].T).T.flatten()
            else:
                Result = p1.T.flatten()

            if tscol1 != tscol2:
                end = -N2 + tsrow2 + 1
                if end == 0: end = None
                return Result[tsrow1:end]
            else:
                return Result[:]


        else:

            Result = np.zeros([t2 - t1 + 1])

            for m in range(m1, m2 + 1 + (m2 < last_model)):
                N = model.models[m].N
                M = model.models[m].M
                start = 0
                end = M * N - 1

                if m == m1:
                    start = t1 - model.models[m].start
                elif m == m1 + 1:
                    start = t1 - model.models[m].start
                    start *= (start > 0)
                if m == m2:
                    end = t2 - model.models[m].start
                elif m == m2 + 1:
                    end = t2 - model.models[m].start

                tscol_i = int(start / N)
                tscol_f = int(end / N)
                tsrow_i = int(start % N)
                tsrow_f = int(end % N)
                tsrow_f = -N + tsrow_f + 1
                if tsrow_f == 0: tsrow_f = None
                i = -i_index + model.models[m].start + tscol_i * N + tsrow_i
                length = N * (tscol_f - tscol_i + 1) + int(tsrow_f or 0) - tsrow_i
                U = model.models[m].Uk[:]
                S = model.models[m].sk[:]
                V = model.models[m].Vk[tscol_i:tscol_f + 1, :]
                p = np.dot(U * S, V.T)
                Result[i:i + length] += 0.5 * p.T.flatten()[tsrow_i:tsrow_f]
            fix_0_index = int(model.T / 2) - i_index
            fix_0_index *= (fix_0_index > 0)

            fix_last_index = t2 - model.models[last_model].start - int(model.T / 2) + 1
            fix_last_index *= (fix_last_index > 0)

            Result[:fix_0_index] = 2 * Result[:fix_0_index]
            if fix_last_index > 0: Result[-fix_last_index:] = 2 * Result[-fix_last_index:]

            return Result[:]