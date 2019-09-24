import numpy as np
import pandas as pd
from tspdb.src.database_module.db_class import Interface
from tspdb.src.pindex.pindex_utils import index_ts_mapper
from scipy.stats import norm

def get_prediction_range( index_name, table_name, value_column, index_col, interface, t1,t2 , uq = True, uq_method ='Gaussian', c = 95.):

    """
    Return an array of N (N = t2-t1+1) predicted value along with the confidence interval for the value of column_name  at time t1 to t2  
    using index_name  by calling either forecast_range or impute_range function 
    ----------
    Parameters
    ----------
    index_name: string 
        name of the PINDEX used to query the prediction

    index_name: table_name 
        name of the time series table in the database

    value_column: string
        name of column than contain time series value

    index_col: string  
        name of column that contains time series index/timestamp

    interface: db_class object
        object used to communicate with the DB. see ../database/db_class for the abstract class
    
    t1: (int or timestamp)
        index or timestamp indicating the start of the queried range 
    
    t2: (int or timestamp)
        index or timestamp indicating the end of the queried range 
    
    uq: boolean optional (default=true) 
        if true,  return upper and lower bound of the  c% confidenc interval

    uq_method: string optional (defalut = 'Gaussian') options: {'Gaussian', 'Chebyshev'}
        Uncertainty quantification method used to estimate the confidence interval

    c: float optional (default 95.)    
        confidence level for uncertainty quantification, 0<c<100
    ----------
    Returns
    ----------
    prediction array, shape [(t1 - t2 +1)  ]
        Values of the predicted point of the time series in the time interval t1 to t2
    
    deviation array, shape [1, (t1 - t2 +1)  ]
        The deviation from the mean to get the desired confidence level 
    """
    # query pindex parameters
    T,T_var, L, k,k_var, L_var, last_model, MUpdateIndex,var_direct, interval, start_ts = interface.query_table( index_name+'_meta',['T','T_var', 'L', 'k','k_var','L_var', 'no_submodels', 'last_TS_inc', 'var_direct_method', 'agg_interval','start_time'])[0]
    last_model -= 1
    
    if not isinstance(t1, (int, np.integer)):
        t1 = pd.to_datetime(t1)
        t2 = pd.to_datetime(t2)
        start_ts = pd.to_datetime(start_ts)
    
    
    interval = float(interval)
    t1 = index_ts_mapper(start_ts, interval, t1)
    t2 = index_ts_mapper(start_ts, interval, t2)
    # check uq variables
    if uq:

        if c < 0 or c >=100:
            raise Exception('confidence interval c must be in the range (0,100): 0 <=c< 100')

        if uq_method == 'Chebyshev':
            alpha = 1./(np.sqrt(1-c/100))
        elif uq_method == 'Gaussian':
            alpha = norm.ppf(1/2 + c/200)
        else:
            raise Exception('uq_method option is not recognized,  available options are: "Gaussian" or "Chebyshev"')
            
    # if all points are in the future, use _get_forecast_range 
    if t1 > (MUpdateIndex - 1):
        if not uq: return _get_forecast_range(index_name,table_name, value_column, index_col, interface, t1,t2, MUpdateIndex,L,k,T,last_model)
        
        else:
            prediction = _get_forecast_range(index_name,table_name, value_column, index_col, interface, t1,t2, MUpdateIndex,L,k,T,last_model)
            var = _get_forecast_range(index_name+'_variance',table_name, value_column, index_col, interface, t1,t2, MUpdateIndex, L,k_var,T_var,last_model)
            # if the second model is used for the second moment, subtract the squared mean to estimate the variance
            if not var_direct:
                var = var - (prediction)**2
            var *= (var>0) 
            
            return prediction, alpha*np.sqrt(var)
    
    # if all points are in the past, use get_imputation_range
    elif t2 <=  MUpdateIndex - 1:    
        if not uq: return _get_imputation_range(index_name, table_name, value_column, index_col, interface, t1,t2,L,k,T,last_model)
        else:
            prediction = _get_imputation_range(index_name, table_name, value_column, index_col, interface, t1,t2,L,k,T,last_model)
            var = _get_imputation_range(index_name+'_variance',table_name, value_column, index_col, interface, t1,t2, L_var,k_var,T_var,last_model)
            # if the second model is used for the second moment, subtract the squared mean to estimate the variance
            if not var_direct:
                var = var - (prediction)**2
            var *= (var>0) 
            return prediction, alpha*np.sqrt(var)
    
    # if points are in both the future and in the past, use both        
    else:
        imputations = _get_imputation_range(index_name, table_name, value_column, index_col, interface, t1,MUpdateIndex-1,L,k,T,last_model)
        forecast = _get_forecast_range(index_name,table_name, value_column, index_col, interface,MUpdateIndex ,t2, MUpdateIndex,L,k,T,last_model)
        if not uq: return list(imputations)+list(forecast)
        
        else:
            imputations_var = _get_imputation_range(index_name+'_variance', table_name, value_column, index_col, interface, t1,MUpdateIndex-1,L_var,k_var,T_var,last_model)
            forecast_var = _get_forecast_range(index_name+'_variance',table_name, value_column, index_col, interface,MUpdateIndex ,t2, MUpdateIndex,L_var,k_var,T_var,last_model)
            if not var_direct:
                forecast_var = forecast_var - (forecast)**2
                imputations_var = imputations_var - (imputations)**2
            imputations_var *= (imputations_var>0)
            forecast_var *= (forecast_var>0)
            return np.array(list(imputations)+list(forecast)), np.array(list(alpha*np.sqrt(imputations_var)) + list(alpha*np.sqrt(forecast_var)))


            


def get_prediction(index_name, table_name, value_column, index_col, interface, t, uq = True, uq_method ='Gaussian', c = 95):
    """
    Return the predicted value along with the confidence interval for the value of column_name  at time t  using index_name 
    by calling either get_forecast or get_imputation function 
    ----------
    Parameters
    ----------
    index_name: string 
        name of the PINDEX used to query the prediction

    index_name: table_name 
        name of the time series table in the database

    value_column: string
        name of column than contain time series value

    index_col: string  
        name of column that contains time series index/timestamp

    interface: db_class object
        object used to communicate with the DB. see ../database/db_class for the abstract class
    
    t: (int or timestamp)
        index or timestamp indicating the queried time. 
    
    uq: boolean optional (default=true) 
        if true,  return upper and lower bound of the  c% confidenc interval

    uq_method: string optional (defalut = 'Gaussian') options: {'Gaussian', 'Chebyshev'}
        Uncertainty quantification method used to estimate the confidence interval

    c: float optional (default 95.)    
        confidence level for uncertainty quantification, 0<c<100
    ----------
    Returns
    ----------
    prediction float
        Values of time series in the time interval start to end sorted according to index_col
    
    deviation float
        The deviation from the mean to get the desired confidence level 
    
    """
    # query pindex parameters

    T,T_var, L, k,k_var, L_var, last_model, MUpdateIndex,var_direct, interval, start_ts = interface.query_table( index_name+'_meta',['T','T_var', 'L', 'k','k_var','L_var', 'no_submodels', 'last_TS_inc', 'var_direct_method', 'agg_interval','start_time'])[0]
    last_model -= 1
    
    if not isinstance(t, (int, np.integer)):
        t = pd.to_datetime(t)
        start_ts = pd.to_datetime(start_ts)
    interval = float(interval)
    t = index_ts_mapper(start_ts, interval, t)
    if uq:
        
        if uq_method == 'Chebyshev':
            alpha = 1./(np.sqrt(1-c/100))
        
        elif uq_method == 'Gaussian':
            alpha = norm.ppf(1/2 + c/200)
        
        else:
            raise Exception('uq_method option is not recognized,  available options are: "Gaussian" or "Chebyshev"')

    if t > (MUpdateIndex - 1):
        if not uq: return _get_forecast_range(index_name,table_name, value_column, index_col, interface,t, t, MUpdateIndex,L,k,T,last_model)[-1]
        else:
            prediction = _get_forecast_range(index_name,table_name, value_column, index_col, interface,t, t, MUpdateIndex,L,k,T,last_model)[-1]
            var = _get_forecast_range(index_name+'_variance',table_name, value_column, index_col, interface,t, t, MUpdateIndex,L_var,k_var,T_var,last_model)[-1]
            
            if not var_direct:
                var = var - (prediction)**2
            var *= (var>0)
            return prediction, alpha*np.sqrt(var)

    else:
        if not uq: return _get_imputation(index_name, table_name, value_column, index_col, interface, t,L,k,T,last_model)
        else:
            prediction = _get_imputation(index_name, table_name, value_column, index_col, interface, t,L,k,T,last_model)
            var = _get_imputation(index_name+'_variance',table_name, value_column, index_col, interface, t, L_var,k_var,T_var,last_model)
            
            if not var_direct:
                var = var - (prediction)**2
            var *= (var>0)
            return prediction, alpha*np.sqrt(var)
            


def _get_imputation_range(index_name, table_name, value_column, index_col, interface, t1,t2,L,k,T,last_model):

    """
    Return the imputed value in the past at the time range t1 to t2 for the value of column_name using index_name 
    ----------
    Parameters
    ----------
    index_name: string 
        name of the PINDEX used to query the prediction

    index_name: table_name 
        name of the time series table in the database

    value_column: string
        name of column than contain time series value

    index_col: string  
        name of column that contains time series index/timestamp

    interface: db_class object
        object used to communicate with the DB. see ../database/db_class for the abstract class
    
    t1: (int or timestamp)
        index or timestamp indicating the start of the queried range 
    
    t2: (int or timestamp)
        index or timestamp indicating the end of the queried range  
    
    L: (int)
        Model parameter determining the number of rows in each matrix in a sub model. 
    
    k: (int )
        Model parameter determining the number of retained singular values in each matrix in a sub model. 
    
    T: (int or timestamp)
        Model parameter determining the number of datapoints in each matrix in a sub model.
    
    last_model: (int or timestamp)
        The index of the last sub model
    ----------
    Returns
    ----------
    prediction  array, shape [(t1 - t2 +1)  ]
        Imputed value of the time series  in the range [t1,t2]  using index_name
     
    
    """
    # map the two boundary points to their sub models

    m1 = int( max((t1) / int(T / 2) - 1, 0))
    m2 = int( max((t2) / int(T / 2) - 1, 0))
    # query the sub-models parameters
    result = interface.query_table( index_name+'_m',['L', 'start', 'N'], 'modelno =' + str(m1) +' or modelno =' + str(m2))
    N1, start1, M1 = result[0]
    
    # if sub-models are different, get the other sub-model's parameters
    if m1 != m2: N2, start2, M2 = result[1]
    else: N2, start2, M2 = result[0]
    
    # Remove when the model writing is fixed (It should write integers directly)
    start1, start2,N1, N2, M1, M2 =  map(int, [start1, start2,N1, N2, M1, M2])
    
    # caluculate tsrow and tscolumn
    if m2 == last_model:
        tscol2 = int((t2 - start2) / N2) + int((start2) / L)
        tsrow2 = (t2 - start2) % N2
    
    else:
        tscol2 = int(t2/N2)
        tsrow2 = t2 % N2

    if m1 == last_model:
        tscol1 = int((t1 - start1) / N1) + int((start1) / L)
        tsrow1 = int((t1 - start1) % N1)
    
    else:
        tscol1 = int(t1/N1)
        tsrow1 = int(t1 % N1)
    # if tscol are the same
    if tscol1 == tscol2:
        ## change to SUV
        S = interface.get_S_row(index_name + '_s', [m1, m2 + 1], k,
                                         return_modelno=True)
        U = interface.get_U_row(index_name + '_u', [tsrow1, tsrow2], [m1, m2 + 1], k,
                                         return_modelno=True)
        V = interface.get_V_row(index_name + '_v', [tscol1, tscol2], k,
                                         [m1, m2 + 1],
                                         return_modelno=True)
        p = np.dot(U[U[:, 0] == m1, 1:] * S[0, 1:], V[V[:, 0] == m1, 1:].T)
        if (m2 != last_model and m1 != 0):
            Result = 0.5 * p.T.flatten() + 0.5 * np.dot(U[U[:, 0] == m1 + 1, 1:] * S[1, 1:],V[V[:, 0] == m1 + 1, 1:].T).T.flatten()
        else:
            Result = p.T.flatten()
        return Result

    else:
        i_index = (t1 - (t1 - start1) % N1)
        end = -N2 + tsrow2 + 1
        # Determine returned array size
        
        Result = np.zeros([t2 + - end  - i_index + 1])
        Count = np.zeros(Result.shape)
        # query relevant tuples
        ## change to SUV
        S = interface.get_S_row(index_name + '_s', [m1, m2 + 1], k,
                                         return_modelno=True)
        U = interface.get_U_row(index_name + '_u', [0, 2 * L], [m1, m2 + 1], k,
                                         return_modelno=True)
        V = interface.get_V_row(index_name + '_v', [tscol1, tscol2], k,
                                         [m1, m2 + 1],
                                         return_modelno=True)
        for m in range(m1, m2 + 1 + (m2 < last_model - 1)):
            p = np.dot(U[U[:, 0] == m, 1:] * S[m - m1, 1:], V[V[:, 0] == m, 1:].T)
            start = start1 + int(T/2)*(m-m1)
            N = N1
            M = M1
            if m == m2:
                N = N2
                M = M2
            if m == last_model:
                finish = t2 - end +1
                if m1 == last_model:
                    i = 0
                    res = p.T.flatten()
                    cursor = i_index
                    length = finish - cursor
                else:
                    cursor = max(start + int(T/2), i_index)
                    res = p.T.flatten()
                    i = cursor - i_index
                    i *= (i > 0)
                    length = finish - cursor
                    res = res[-length:]

            else:
                cursor = max(start, i_index)
                finish = start + M * N
                res = p.T.flatten()
                i = cursor - i_index
                i *= (i>0)
                length = finish - cursor

            Result[i:i + length] += 0.5 * res
            Count [i:i + length] += 1
        Result[Count == 1] *= 2



        if end == 0: end = None
        return Result[tsrow1:end]


def _get_forecast_range(index_name,table_name, value_column, index_col, interface, t1, t2,MUpdateIndex,L,k,T,last_model, averaging = 'average'):
    """
    Return the florcasted value in the past at the time range t1 to t2 for the value of column_name using index_name 
    ----------
    Parameters
    ----------
    index_name: string 
        name of the PINDEX used to query the prediction

    index_name: table_name 
        name of the time series table in the database

    value_column: string
        name of column than contain time series value

    index_col: string  
        name of column that contains time series index/timestamp

    interface: db_class object
        object used to communicate with the DB. see ../database/db_class for the abstract class
    
    t1: (int or timestamp)
        index or timestamp indicating the start of the queried range 
    
    t2: (int or timestamp)
        index or timestamp indicating the end of the queried range  
    
    L: (int)
        Model parameter determining the number of rows in each matrix in a sub model. 
    
    k: (int )
        Model parameter determining the number of retained singular values in each matrix in a sub model. 
    
    T: (int or timestamp)
        Model parameter determining the number of datapoints in each matrix in a sub model.
    
    last_model: (int or timestamp)
        The index of the last sub model

    averaging: string, optional, (default 'average')
        Coefficients used when forecasting, 'average' means use the average of all sub models coeffcients. 
    ----------
    Returns
    ----------
    prediction  array, shape [(t1 - t2 +1)  ]
        forecasted value of the time series  in the range [t1,t2]  using index_name
    """
    # get coefficients
    coeffs = np.array(interface.get_coeff(index_name + '_c_view', averaging))
    no_coeff = len(coeffs)
    
    # the forecast should always start at the last point
    t1_ = MUpdateIndex 
    output = np.zeros([t2 - t1_ + 1 + no_coeff])
    output[:no_coeff] = _get_imputation_range(index_name, table_name, value_column, index_col, interface, t1_ - no_coeff, t1_ - 1, L,k,T,last_model)
    for i in range(0, t2 + 1 - t1_):
        output[i + no_coeff] = np.dot(coeffs.T, output[i:i + no_coeff])
    return output[-(t2 - t1 + 1):]
    


def _get_imputation(index_name, table_name, value_column, index_col, interface, t,L,k,T,last_model):
    """
    Return the imputed value in the past at time t for the value of column_name using index_name 
        ----------
        Parameters
        ----------
        index_name: string 
            name of the PINDEX used to query the prediction

        index_name: table_name 
            name of the time series table in the database

        value_column: string
            name of column than contain time series value

        index_col: string  
            name of column that contains time series index/timestamp

        interface: db_class object
            object used to communicate with the DB. see ../database/db_class for the abstract class
        
        t: (int or timestamp)
            index or timestamp indicating the queried time. 
        
        L: (int)
            Model parameter determining the number of rows in each matrix in a sub model. 
        
        k: (int )
            Model parameter determining the number of retained singular values in each matrix in a sub model. 
        
        T: (int or timestamp)
            Model parameter determining the number of datapoints in each matrix in a sub model.
        
        last_model: (int or timestamp)
            The index of the last sub model
        ----------
        Returns
        ----------
        prediction float
            Imputed value of the time series at time t using index_name
         
        
    """
    # map t to the right sub model
    modelNo = int( max((t) / int(T / 2) - 1, 0))
    N = L
    # if it is in the last sub-model, tscol and tsrow will be calculated differently
    if modelNo == last_model:

        N, last_model_start = interface.query_table( index_name+'_m',['L', 'start'], ' modelno =' + str(modelNo) )[0]
        tscolumn = int((t - last_model_start) / N) + int((last_model_start) / L)
        tsrow = (t - last_model_start) % N
        U, V, S = interface.get_SUV(index_name, [tscolumn, tscolumn], [tsrow, tsrow],
                                            [modelNo, modelNo + 1], k)

        return sum([a * b * c for a, b, c in zip(U[0, :], S[0, :], V[0, :])])
        # U

    # if it is in the model before last, do not query the last model
    elif modelNo == last_model - 1:
        tscolumn = int(t / N)
        tsrow = t % N
        U, V, S = interface.get_SUV(index_name, [tscolumn, tscolumn], [tsrow, tsrow],
                                            [modelNo, modelNo], k)
        return sum([a * b * c for a, b, c in zip(U[0, :], S[0, :], V[0, :])])
    
    else:
        tscolumn = int(t / N)
        tsrow = t % N

        U, S, V = interface.get_SUV(index_name, [tscolumn, tscolumn], [tsrow, tsrow],
                                            [modelNo, modelNo + 1], k)
  
        # if two sub models are queried get the average
        if V.shape[0] == 2 and U.shape[0] == 2:
            return 0.5* np.sum(U * S * V) 
            #return 0.5 * (sum([a * b * c for a, b, c in zip(U[0, :], S[0, :], V[0, :])]) + sum(
            #    [a * b * c for a, b, c in zip(U[1, :], S[1, :], V[1, :])]))
        
        # else return one value directly
        return sum([a * b * c for a, b, c in zip(U[0, :], S[0, :], V[0, :])])
        